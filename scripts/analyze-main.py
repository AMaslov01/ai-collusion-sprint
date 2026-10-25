"""Offline paired main analysis after exact production actor/component cache replay."""

import argparse
from hashlib import sha256
import os
from pathlib import Path

from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.main_analysis import analyze
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
    audit = read_json(args.cache_audit)
    require(audit["schema"] == "main_cache_replay_v1" and audit["status"] == "passed_cache_only"
            and audit["manifest_sha256"] == fingerprint(manifest) and audit["new_model_calls"] == 0,
            "Matching successful production cache replay is required")
    for name, digest in audit["input_sha256"].items():
        path = root / name
        require(path.resolve().is_relative_to(root) and sha256(path.read_bytes()).hexdigest() == digest,
                "Canonical analysis input changed after replay")
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
