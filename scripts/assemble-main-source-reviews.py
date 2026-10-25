"""Assemble complete independently sealed AI reviews without changing labels.

Administrative CPU work only. Inputs are exact formalization receipts, not model
outputs or scores. A missing, duplicate or mismatched assigned row stops assembly.
The frozen main validator checks the assembled packet before it can be admitted.
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.main_cycle import (
    file_hash, registry_digest, require, verify_ai_annotations,
)


FORMALIZER_SHAS = {
    "f840ab8586ee1cf0b84abfc4be8f56efb655b203c41990a532ec77e19fdebfd2",
    "2fa1666c99bd2f50e56ee1a85b05f76e8b173f382edecd01521070ede2c6fcf9",
}
REVIEWERS = {"Root": "Root", "Diagnostics": "Diagnostics/01a16da8-0275-76e0-8936-770c4df31616"}


def checked_path(root, name, expected_hash):
    path = root / name
    require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and
            file_hash(path) == expected_hash, "Sealed review file changed or escaped its directory")
    return path


def assemble(manifest, manifest_sha, prefixes, reports, inventory, output):
    assigned = [str(number) for number in manifest["sample"]["group_ids"]]
    require(len(assigned) == len(set(assigned)) == manifest["sample"]["assigned_group_count"],
            "Frozen assigned group inventory is inconsistent")
    require(fingerprint(manifest) == manifest_sha == inventory["manifest_sha256"] and
            inventory["schema"] == "main_source_formalized_batch_inventory_v1",
            "Inventory belongs to another frozen manifest")
    rows = {key: {} for key in REVIEWERS}
    references, input_hashes = {}, {}
    for entry in inventory["batches"]:
        path = checked_path(reports, entry["file"], entry["sha256"])
        require(entry["file"] not in input_hashes, "Duplicate formalization receipt")
        input_hashes[entry["file"]] = entry["sha256"]
        report = read_json(path)
        key = next((key for key, identity in REVIEWERS.items() if identity == report["reviewer_id"]), None)
        require(key is not None and report["schema"] == "independent_AI_main_source_review_batch_v1" and
                report["role"] == "assistant" and report["human_gold"] is False and
                report["source_only_before_memory"] is True and report["independently_locked"] is True and
                report["manifest_sha256"] == manifest_sha and report["status"] == "passed" and
                report["errors"] == [] and report["new_model_calls"] == 0 and
                report["semantic_labels_changed"] is False and report["formalizer_sha256"] in FORMALIZER_SHAS,
                "Unverified or semantically changed formalization receipt")
        for number, original in report["groups"].items():
            require(number in assigned and number not in rows[key], "Duplicate or unassigned reviewer row")
            require(original["semantic_draft_sha256"] == report["semantic_draft_sha256"] and
                    original["Q_mapping_binding_status"] == "verified_against_independent_source_meanings",
                    "Formalized row lost its independent semantic lock")
            name, digest = original["reference_file"], original["reference_sha256"]
            require(name == f"source-references/{key}/group-{number}.json" and name not in references,
                    "Unexpected or duplicate reference name")
            reference = checked_path(path.parent, name, digest)
            binding = read_json(reference)
            require(binding["source_file_sha256"] == original["source_file_sha256"] and
                    binding["Q_mapping_sha256"] == original["Q_mapping_sha256"] and
                    binding["semantic_draft_sha256"] == original["semantic_draft_sha256"] and
                    binding["reviewer_id"] == report["reviewer_id"] and
                    binding["private_Q_verification_after_independent_semantic_lock"] is True and
                    binding["memory_outputs_opened"] is False and binding["human_gold"] is False,
                    "Reference no longer binds the independently reviewed source and Q")
            rows[key][number] = deepcopy(original)
            references[name] = (reference, digest)
    require(all(set(review) == set(assigned) for review in rows.values()),
            "Both reviewers must cover every frozen assigned group before assembly")
    bound = {}
    for number in assigned:
        folder = prefixes / f"prefix/group-{number}"
        require(read_json(folder / "status.json")["status"] == "prefix_collected", "Assigned prefix incomplete")
        bound[number] = read_json(folder / "private-registry-bound.json")
        for review in rows.values():
            require(review[number]["private_bound_registry_sha256"] == file_hash(folder / "private-registry-bound.json"),
                    "Native binding changed after independent Q verification")
    require(not output.exists(), "Preserve existing assembly; use a new reviewed output directory")
    output.mkdir(parents=True, mode=0o700)
    for name, (source, digest) in references.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
        require(file_hash(destination) == digest, "Copied reference changed bytes")
    common = {"role": "assistant", "human_gold": False, "source_only_before_memory": True,
              "manifest_sha256": manifest_sha, "registry_sha256": registry_digest(bound)}
    specs = []
    for key, identity in REVIEWERS.items():
        name = f"source-review-{key}.json"
        review = {**common, "schema": "independent_AI_main_source_review_v1", "reviewer_id": identity,
                  "independently_locked": True, "groups": {n: rows[key][n] for n in assigned},
                  "formalization_receipts_sha256": input_hashes, "human_audit_status": "pending"}
        atomic_json(output / name, review)
        specs.append({"reviewer_id": identity, "review_file": name, "review_sha256": file_hash(output / name)})
    merged = {}
    disagreements = []
    for number in assigned:
        left, right = (rows[key][number] for key in REVIEWERS)
        for field in ("source_file_sha256", "Q_mapping_sha256", "private_bound_registry_sha256"):
            require(left[field] == right[field], "Independent reviews bind different source/Q identities")
        formation = left["formation_status"] if left["formation_status"] == right["formation_status"] else "uncertain"
        checkpoint = left["checkpoint_status"] if left["checkpoint_status"] == right["checkpoint_status"] else "uncertain"
        if left["formation_status"] != right["formation_status"] or left["checkpoint_status"] != right["checkpoint_status"]:
            disagreements.append(number)
        name = f"source-references/merged/group-{number}.json"
        binding = {"schema": "independent_AI_main_source_consensus_reference_v1",
                   "source_file_sha256": left["source_file_sha256"], "Q_mapping_sha256": left["Q_mapping_sha256"],
                   "memory_outputs_opened": False, "human_gold": False,
                   "independent_references": [{"reviewer_id": REVIEWERS[key], "file": rows[key][number]["reference_file"],
                                               "sha256": rows[key][number]["reference_sha256"]} for key in REVIEWERS],
                   "merge_policy": "Unanimous label retained; any disagreement remains uncertain. Original reviews unchanged."}
        atomic_json(output / name, binding)
        merged[number] = {"source_file_sha256": left["source_file_sha256"], "Q_mapping_sha256": left["Q_mapping_sha256"],
                          "formation_review_complete": True, "whole_source_reviewed": True,
                          "formation_status": formation, "checkpoint_status": checkpoint,
                          "harmful_future_agreement_present": True if formation == "confirmed" else False if formation == "absent" else None,
                          "reference_file": name, "reference_sha256": file_hash(output / name)}
    annotations = {**common, "schema": "independent_AI_main_source_annotations_v1", "reviewers": specs,
                   "groups": merged, "human_audit_status": "pending", "all_assigned_groups_retained": True,
                   "disagreement_group_ids": disagreements}
    atomic_json(output / "ai-source-annotations.json", annotations)
    # This is the existing validator from the immutable inference snapshot. No
    # runtime checks or label rules are replaced by this administrative helper.
    verify_ai_annotations(output, manifest, bound, prefixes)
    receipt = {"schema": "main_source_review_assembly_receipt_v1", "status": "passed_frozen_validator",
               **common, "assigned_group_count": len(assigned), "reviewer_count": len(REVIEWERS),
               "disagreement_group_ids": disagreements, "semantic_labels_changed": False, "new_model_calls": 0,
               "source_inventory_sha256": fingerprint(inventory), "input_receipts_sha256": input_hashes,
               "annotations_sha256": file_hash(output / "ai-source-annotations.json"),
               "assembler_sha256": file_hash(Path(__file__))}
    atomic_json(output / "assembly-receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "prefixes", "reports", "inventory", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ("manifest-sha256", "inventory-sha256"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    require(os.getuid() != 0 and Path("/.dockerenv").exists() and os.environ.get("CUDA_VISIBLE_DEVICES") == "",
            "Nonroot Docker with empty child CUDA mask required")
    require(file_hash(args.inventory) == args.inventory_sha256, "Formalization inventory changed")
    manifest = read_json(args.manifest)
    root = args.prefixes.parent
    require(not (root / "continuations").exists(), "Independent source assembly must precede all MAIN memories/futures")
    receipt = assemble(manifest, args.manifest_sha256, args.prefixes, args.reports, read_json(args.inventory), args.output)
    print(json.dumps({k: receipt[k] for k in ("status", "assigned_group_count", "reviewer_count", "annotations_sha256", "new_model_calls")}))


if __name__ == "__main__":
    main()
