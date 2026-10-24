"""Run locked independent mapping inputs and unchanged-wire regressions on CPU."""

import argparse
from hashlib import sha256
import os
from pathlib import Path

from collusion_handoff.abe_cycle import actor_preflight
from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.city_labels import pinned_dependencies, resolve_initial_city
from collusion_handoff.memory import require
from collusion_handoff.readiness_cycle import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--fixtures-sha256", required=True)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require(os.getuid() != 0 and Path("/.dockerenv").exists() and os.environ.get("CUDA_VISIBLE_DEVICES") == "", "Non-root CPU-only Docker required")
    require(not args.output.exists(), "Preserve prior mapping probes")
    require(sha256(args.fixtures.read_bytes()).hexdigest() == args.fixtures_sha256, "Independent input lock changed")
    config, base, assets = settings()
    policy = config["task_policy"]["city_normalization"]
    _, normalize = pinned_dependencies(policy)
    observations = []
    for case in read_json(args.fixtures)["cases"]:
        source = case["input"]
        before = fingerprint(source)
        try:
            receipt = resolve_initial_city(source["first_accommodation"], {item["city"] for item in source["canonical_catalogue_items"]}, source["city_state_pairs"], normalize)
            actual = {"mapping_status": "accepted", "city": receipt["city"], "selected_day_index": receipt["selected_day_index"]}
            detail = receipt
        except Exception as error:
            actual = {"mapping_status": "unsupported", "city": None, "selected_day_index": None}
            detail = {"type": type(error).__name__, "message": str(error)}
        actual["source_accommodation_unchanged"] = fingerprint(source) == before
        observations.append({"case_id": case["case_id"], "input_sha256": before, "actual": actual, "details": detail})
    atomic_json(args.output / "independent-observations.json", {"input_file_sha256": args.fixtures_sha256, "cases": observations,
                "expected_results_used_by_adapter": False, "new_model_calls": 0})
    actor_preflight(config, base, assets, args.output / "exact-regression", [4, 5])
    old = read_json(args.baseline / "actor-wire-probes.json")
    current = read_json(args.output / "exact-regression/actor-wire-probes.json")
    require(current == old, "Exact-mode actual actor requests changed")
    old_groups = read_json(args.baseline / "private-task-registry.json")["groups"]
    new_groups = read_json(args.output / "exact-regression/private-task-registry.json")["groups"]
    for number in ("4", "5"):
        for field in ("base_person", "tasks", "cap", "private_feasibility_witness", "Q_source_mapping"):
            require(new_groups[number][field] == old_groups[number][field], "Exact-mode scientific registry changed: " + field)
    actor_preflight(config, base, assets, args.output / "known-static-6-7", [6, 7])
    atomic_json(args.output / "probe-status.json", {"status": "completed_CPU_mapping_probes_no_model_calls", "code_commit": os.environ["ADAPTER_COMMIT"],
                "config_sha256": sha256(Path("/code/configs/readiness-cycle-v5.json").read_bytes()).hexdigest(), "normalization_policy": policy,
                "independent_cases_observed": len(observations), "independent_expected_comparison": "External independent reviewer; not performed by this adapter",
                "exact_4_5_registry_and_full_wires_unchanged": True, "groups6_7": "Known static sources, model outcomes unseen; pre-model registry only",
                "accepted_registry_is_not_GPU_dispatch": True, "new_model_calls": 0, "main_started": False})


if __name__ == "__main__":
    main()
