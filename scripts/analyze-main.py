"""Offline paired main analysis after exact production actor/component cache replay."""

import argparse
from hashlib import sha256
import os
from pathlib import Path
import sys

sys.path.insert(0, "/code/src")

from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.main_analysis import (analyze, verify_analysis_review_binding,
    verify_analysis_run_binding, verify_executing_analysis_driver)
from collusion_handoff.main_cycle import annotation_receipt_name, approved_pipeline, verify_annotations
from collusion_handoff.paired_analysis import require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "review", "cache-audit", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    require(os.getuid() != 0 and Path("/.dockerenv").exists()
            and os.environ.get("CUDA_VISIBLE_DEVICES") == ""
            and not list(Path("/dev").glob("nvidia[0-9]*"))
            and {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}, "Nonroot CPU Docker/network=none required")
    root = args.run.resolve()
    require(not args.output.exists() and root != args.output.resolve() and root not in args.output.resolve().parents,
            "Use a new immutable analysis outside the input run")
    manifest, config, _, _, _ = approved_pipeline(args.review, Path("/code"))
    verify_executing_analysis_driver(__file__, manifest["analysis"]["analysis_code_sha256"])
    audit = read_json(args.cache_audit)
    require(audit["schema"] == "main_cache_replay_v1" and audit["status"] == "passed_cache_only"
            and audit["manifest_sha256"] == fingerprint(manifest) and audit["new_model_calls"] == 0,
            "Matching successful production cache replay is required")
    verify_analysis_review_binding(audit, args.review)
    required_paths = ["main-manifest.json", "ledger/requests.jsonl", "ledger/responses.jsonl",
        "cpu-prepare/assigned-future-opportunities.json", "continuations/all-assigned-results.json",
        "prefixes/phase-manifest.json", "continuations/phase-manifest.json"]
    for number in manifest["sample"]["group_ids"]:
        required_paths.extend(f"prefixes/prefix/group-{number}/" + name
            for name in ("source.json", "snapshot.json", "private-registry-bound.json"))
        required_paths.extend(f"prefixes/prefix/group-{number}/results/g{number}-t{index}.json"
                              for index in manifest["sample"]["prefix_task_indices"])
    verify_analysis_run_binding(audit, root, required_paths)
    assignments = read_json(root / "cpu-prepare/assigned-future-opportunities.json")
    outcomes = read_json(root / "continuations/all-assigned-results.json")["observed"]
    groups, prefix_results = {}, {}
    for number in manifest["sample"]["group_ids"]:
        prefix = root / f"prefixes/prefix/group-{number}"
        groups[number] = read_json(prefix / "private-registry-bound.json")
        prefix_results[number] = [read_json(prefix / "results" / (task["task_id"] + ".json"))["evaluation"]["H"]
                                  for task in groups[number]["tasks"][:2]]
    annotation = verify_annotations(args.review, manifest, groups, root / "prefixes")
    report = analyze(manifest, assignments, outcomes, annotation, prefix_results)
    report.update(manifest_sha256=fingerprint(manifest), cache_audit_sha256=sha256(args.cache_audit.read_bytes()).hexdigest(),
        source_annotation_sha256=sha256((args.review / annotation_receipt_name(manifest)).read_bytes()).hexdigest(),
        analysis_code_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), new_model_calls=0,
        numpy_version=next(iter(report["conditions"].values()))["all_assigned_eligible_prefixes"]["final_canonical_metrics"]["numpy_version"])
    os.umask(0o077)
    atomic_json(args.output, report)
    print({"status": "analyzed", "human_conditional_claims": "pending_human_audit", "groups": len(groups)})


if __name__ == "__main__":
    main()
