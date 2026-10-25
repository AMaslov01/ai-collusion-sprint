"""Requested descriptive interim H(S)-H(CVP), never a MAIN stopping gate."""

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sys


HELPER_SHA = "496a80dd53331fcb6abbcdd9adb270888570c27805fe968139a6a0779a7ef386"
PAIRED_SHA = "d44283c4eeab03786719cda6eec20351fb114b3533ab64329057fdd1212c98d6"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("snapshot", "code", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    require(os.getuid() != 0 and Path("/.dockerenv").is_file()
            and os.environ.get("CUDA_VISIBLE_DEVICES") == "" and sys.dont_write_bytecode,
            "Existing nonroot worker with child CUDA mask and -B required")
    require(all(os.environ.get(key) == "1" for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")),
            "Set native CPU NumPy thread ceilings before import")
    snapshot, code, output = (p.resolve() for p in (args.snapshot, args.code, args.output))
    require(not output.exists() and output.parent.is_dir()
            and not any(output.is_relative_to(p) or p.is_relative_to(output) for p in (snapshot, code)),
            "New external bootstrap output required")
    os.umask(0o077)

    def guard(event, values):
        if event.startswith(("socket.", "subprocess.")) or event in {"os.system", "os.exec", "os.posix_spawn"}:
            raise RuntimeError("Network and subprocess operations prohibited")
        if event == "open":
            path, mode, flags = values
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
            if writing:
                require(isinstance(path, (str, bytes, os.PathLike)) and Path(path).resolve().is_relative_to(output), "Write outside bootstrap output")
        if event in {"os.remove", "os.rename", "os.rmdir"}:
            raise RuntimeError("Removal/renaming prohibited")
        if event == "os.mkdir":
            require(Path(values[0]).resolve().is_relative_to(output), "Directory outside bootstrap output")
    sys.addaudithook(guard)
    hashes = {}

    def read(name):
        path = snapshot / name
        require(not path.is_symlink(), "Snapshot symlink")
        raw = path.read_bytes()
        hashes[name] = sha256(raw).hexdigest()
        return json.loads(raw)

    frozen = read("interim.json")
    records = read("opportunities.json")
    inventory = read("input-sha256.json")
    require(frozen["schema"] == "descriptive_interim_closed_groups_v1" and frozen["helper_sha256"] == HELPER_SHA,
            "Use the exact reviewed fresh descriptive helper snapshot")
    require(frozen["inference_commit"] == "a0548b748c7c749ae8c75baa86aec4f207f86375"
            and frozen["assigned_full_run"] == 948 and frozen["human_gold"] is False,
            "Wrong frozen MAIN or label authority")
    module_path = code / "src/collusion_handoff/paired_analysis.py"
    require(sha256(module_path.read_bytes()).hexdigest() == PAIRED_SHA
            and inventory[str(module_path)] == PAIRED_SHA, "Frozen paired arithmetic implementation differs")
    spec = importlib.util.spec_from_file_location("frozen_interim_paired_arithmetic", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    groups = frozen["closed_group_ids_frozen_order"]
    require(groups and len(set(groups)) == len(groups) and len(records) == len(groups) * 12,
            "Incomplete closed-group snapshot")
    require(len({(r["group_id"], r["arm"], r["task_id"]) for r in records}) == len(records), "Duplicate assigned result")
    by_group = {g["group_id"]: g for g in frozen["groups"]}
    conditional = [g for g in groups if by_group[g]["harmful_future_agreement_present"] is True
                   and by_group[g]["checkpoint_status"] == "active" and 1 in by_group[g]["prefix_H"]]
    require(conditional == frozen["provisional_primary_condition_groups"], "Sealed pre-treatment population differs")
    selected = [r for r in records if r["arm"] in ("S", "CVP")]
    require(len(selected) == len(groups) * 4 and all(
        {r["task_id"] for r in selected if r["group_id"] == g and r["arm"] == a} == {f"g{g}-t3", f"g{g}-t4"}
        for g in groups for a in ("S", "CVP")), "Lost assigned arm/task denominator")
    assignments = [{k: r[k] for k in ("group_id", "arm", "task_id")} for r in selected]
    # Only H is projected; U/Q are intentionally not analyzed or published here.
    outcomes = [{**a, "result": {"task_id": a["task_id"], "status": r["status"], "evaluation": {"H": r["H"]}}}
                for a, r in zip(assignments, selected)]
    populations = {}
    for name, ids in (("all_closed_assigned_groups", groups), ("AI_confirmed_active_with_prefix_H1", conditional)):
        raw = module.summarize(assignments, outcomes, population_group_ids=ids,
            contrasts=[{"metric": "H", "minuend": "S", "subtrahend": "CVP"}],
            bootstrap_draws=10000, seed=20261026, confidence_level=0.95,
            independent_group_sampling_assumed=False, finite_population_random_sampling_assumed=False)
        contrast = raw["contrasts"][0] if raw["contrasts"] else None
        if contrast is not None:
            endpoints = contrast["identification_endpoint_bootstrap_CIs"]
            if endpoints is None:
                endpoints = {key: contrast["paired_group_percentile_CI"] for key in ("lower_endpoint", "upper_endpoint")}
            contrast = {**contrast, "identification_endpoint_bootstrap_CIs": endpoints,
                        "endpoint_CI_outer_envelope_not_latent_point_CI": [endpoints["lower_endpoint"][0], endpoints["upper_endpoint"][1]]}
        populations[name] = {"group_ids": ids, "group_count": len(ids), "assigned_tasks_per_arm": len(ids) * 2,
            "arms": {a: {"statuses": raw["arms"][a]["statuses"], "H": raw["arms"][a]["metrics"]["H"]} for a in raw["arms"]},
            "contrast": contrast, "warnings": raw["warnings"], "numpy_version": raw["numpy_version"]}
    for name, digest in hashes.items():
        require(sha256((snapshot / name).read_bytes()).hexdigest() == digest, "Snapshot changed during bootstrap")
    report = {"schema": "requested_descriptive_interim_H_bootstrap_v1", "created_UTC": datetime.now(timezone.utc).isoformat(),
        "snapshot_started_UTC": frozen["snapshot_started_UTC"], "snapshot_finished_UTC": frozen["snapshot_finished_UTC"],
        "snapshot_path": str(snapshot), "snapshot_files_sha256": hashes, "helper_sha256": HELPER_SHA,
        "driver_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "frozen_paired_module_sha256": PAIRED_SHA,
        "bootstrap": {"draws": 10000, "rng": "numpy.PCG64", "seed": 20261026, "confidence_level": 0.95,
                      "quantile_method": "linear", "cluster": "source_group", "paired_same_indices_all_arms_and_tasks": True},
        "populations": populations, "assigned_H_rows": [{**a, "H": r["H"], "status": r["status"]} for a, r in zip(assignments, selected)],
        "new_model_calls": 0, "protocol_changed": False, "human_gold": False,
        "execution_context": {"UID": os.getuid(), "CUDA_VISIBLE_DEVICES": "", "isolated_CPU": False, "shared_worker_cgroup": True, "native_CPU_NumPy": True, "OMP_OPENBLAS_MKL_threads": 1},
        "limitations": ["Requested interim descriptive bootstrap of a time-ordered incomplete MAIN panel, not the final frozen cache audit/analysis.",
                        "Unknown assigned outcomes remain [0,1]; estimate is null when unidentified, never complete-case or zero imputation.",
                        "Endpoint percentile intervals are pointwise approximate bootstrap intervals; their outer envelope is not a latent point-effect CI or a jointly calibrated 95% region.",
                        "Repeated interim looks have no alpha-spending or optional-stopping protection; no significance, stopping, efficacy or safety decision is authorized.",
                        "AI formation labels remain provisional pending actual human audit; no new outcome-selected subgroup.",
                        "Frozen summarize internally processes other metric slots, but this H-only projection publishes no secondary metrics or p-values."]}
    output.mkdir(mode=0o700)
    with (output / "bootstrap.json").open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    with (output / "contrast-summary.csv").open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["population", "groups", "assigned_per_arm", "estimate", "identification_bounds", "lower_endpoint_CI", "upper_endpoint_CI", "outer_envelope_not_point_CI"])
        for name, item in populations.items():
            c = item["contrast"] or {}
            endpoints = c.get("identification_endpoint_bootstrap_CIs", {})
            writer.writerow([name, item["group_count"], item["assigned_tasks_per_arm"], c.get("estimate"),
                c.get("identification_bounds"), endpoints.get("lower_endpoint"), endpoints.get("upper_endpoint"), c.get("endpoint_CI_outer_envelope_not_latent_point_CI")])
    print(json.dumps({"status": "requested_interim_bootstrap_only", "closed_groups": len(groups), "conditional_groups": len(conditional), "output": str(output), "new_model_calls": 0}))


if __name__ == "__main__":
    main()
