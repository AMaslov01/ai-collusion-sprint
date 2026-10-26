"""Export only numeric outcomes and source-label categories from audited MAIN.

Run in the isolated caniculus CPU container. No prompts, queries, names, catalog
rows, model messages or free-text source annotations enter the public projection.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from collusion_handoff.main_analysis import analyze, verify_analysis_run_binding, verify_analysis_review_binding
from collusion_handoff.artifacts import read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "review", "audit", "analysis", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from public_checks import require_isolated_cpu
    require_isolated_cpu()
    if args.output.exists():
        raise ValueError("Use a new output")
    audit = read_json(args.audit)
    if (audit["status"], audit["calls_replayed"], audit["new_model_calls"], audit["errors"]) != ("passed_cache_only", 4909, 0, []):
        raise ValueError("Expected the frozen successful MAIN audit")
    if sha256(args.analysis.read_bytes()).hexdigest() != "5bbbb4cb2a1aac9f37059b1db1e246e8673388938b475953c34cbc95cd1ceaa9":
        raise ValueError("Unexpected frozen analysis")
    verify_analysis_run_binding(audit, args.run, ["main-manifest.json"])
    verify_analysis_review_binding(audit, args.review)
    manifest = read_json(args.run / "main-manifest.json")
    raw_labels = read_json(args.review / "ai-source-annotations.json")
    project = lambda row, keys: {key: row[key] for key in keys}
    labels = project(raw_labels, ("role", "human_gold", "source_only_before_memory"))
    labels["groups"] = {key: project(row, ("harmful_future_agreement_present", "checkpoint_status"))
                        for key, row in raw_labels["groups"].items()}
    assignments = [project(row, ("group_id", "arm", "task_id")) for row in
                   read_json(args.run / "cpu-prepare/assigned-future-opportunities.json")]
    fields = ("H", "U", "Q", "Q_applicable", "initial_proposal_Q", "ledger_complete",
              "measurement_errors", "initial_proposal_error")
    outcomes = []
    for row in read_json(args.run / "continuations/all-assigned-results.json")["observed"]:
        result = row["result"]
        values = result["evaluation"]
        # The analysis only uses truthiness of error fields; no error prose escapes.
        evaluation = {key: (bool(values.get(key)) if key in ("measurement_errors", "initial_proposal_error")
                            else values[key]) for key in fields if key in values}
        outcomes.append({**project(row, ("group_id", "arm", "task_id")),
                         "result": {"task_id": row["task_id"], "status": result["status"], "evaluation": evaluation}})
    prefix = {str(group): [read_json(args.run / f"prefixes/prefix/group-{group}/results/g{group}-t{task}.json")["evaluation"]["H"]
                          for task in (1, 2)] for group in manifest["sample"]["group_ids"]}
    slim = project(manifest, ("analysis", "science", "sample"))
    # Science/sample are frozen administrative specifications, not task text.
    report = analyze(slim, assignments, outcomes, labels, {int(k): v for k, v in prefix.items()})
    frozen = read_json(args.analysis)
    if any(frozen[key] != value for key, value in report.items()):
        raise ValueError("Public numeric projection changes frozen analysis")
    payload = {"schema": "memcontrast_public_analysis_inputs_v1", "manifest": slim,
               "assignments": assignments, "outcomes": outcomes, "annotation": labels,
               "prefix_H": prefix, "original_analysis_sha256": sha256(args.analysis.read_bytes()).hexdigest(),
               "original_audit_sha256": sha256(args.audit.read_bytes()).hexdigest()}
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps({"status": "exported_numeric_projection", "assigned": len(assignments),
                      "all_frozen_analysis_fields_equal": True}))


if __name__ == "__main__":
    main()
