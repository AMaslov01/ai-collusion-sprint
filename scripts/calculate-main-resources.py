"""Apply the preregistered DEV duration rule before any main model outcomes."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prefix", "handoff", "global-root", "science", "permutation", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--science-sha256", required=True)
    parser.add_argument("--permutation-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    assert os.getuid() != 0 and Path("/.dockerenv").exists()
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == "" and not list(Path("/dev").glob("nvidia[0-9]*"))
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
    assert not args.output.exists()
    inputs = {}

    def read(path):
        raw = path.read_bytes()
        inputs[str(path)] = sha256(raw).hexdigest()
        return json.loads(raw)

    def rows(path):
        raw = path.read_bytes() if path.exists() else b""
        inputs[str(path)] = sha256(raw).hexdigest()
        assert not raw or raw.endswith(b"\n")
        return [json.loads(line) for line in raw.splitlines()]

    science, permutation = read(args.science), read(args.permutation)
    assert inputs[str(args.science)] == args.science_sha256 == permutation["science_plan_sha256"]
    assert inputs[str(args.permutation)] == args.permutation_sha256
    assert permutation["N"] is None and permutation["main_sample_chosen"] is False
    cutoff = datetime.fromisoformat(science["resources"]["absolute_collection_cutoff_utc"])
    assert cutoff == datetime(2026, 10, 26, 9, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    prefix_phase, handoff_phase = (read(path / "phase-manifest.json") for path in (args.prefix, args.handoff))
    assert prefix_phase["code_commit"] == handoff_phase["code_commit"]
    assert prefix_phase["config_sha256"] == handoff_phase["config_sha256"]
    assert prefix_phase["model"] == handoff_phase["model"] and prefix_phase["operations"] == handoff_phase["operations"]
    for folder in (args.prefix, args.handoff):
        terminal = read(folder / "phase-status.json")
        assert terminal["status"] == "finished_with_explicit_statuses" and terminal["failure"] is None
        assert not terminal["accounting_errors"]
    requests = rows(args.global_root / "ledger/requests.jsonl")
    responses = rows(args.global_root / "ledger/responses.jsonl")
    request_map = {row["logical_call_id"]: row for row in requests}
    response_map = {row["logical_call_id"]: row for row in responses}
    assert len(request_map) == len(requests) and len(response_map) == len(responses) and request_map.keys() == response_map.keys()
    assert all(row.get("accounted_actual_tokens") is not None for row in responses)

    def job(folder):
        req, resp = rows(folder / "io/requests.jsonl"), rows(folder / "io/responses.jsonl")
        assert {r["logical_call_id"] for r in req} == {r["logical_call_id"] for r in resp}
        for row in req:
            assert request_map[row["logical_call_id"]] == row and row["attempt"] == 1
            assert sha256(row["body_utf8"].encode()).hexdigest() == row["request_sha256"]
        for row in resp:
            assert response_map[row["logical_call_id"]] == row and row["status_code"] == 200
            assert row["request_sha256"] == request_map[row["logical_call_id"]]["request_sha256"]
            assert math.isfinite(row["duration_seconds"]) and row["duration_seconds"] >= 0
        return req

    def duration(req):
        return sum(response_map[row["logical_call_id"]]["duration_seconds"] for row in req)

    source_order = read(args.prefix / "assignment.json")["selected_worlds"]
    assert source_order == [8, 9]
    prefix_requests = [row for number in source_order for row in job(args.prefix / f"prefix/group-{number}")]
    selected = read(args.handoff / "source-selection.json")["selected_world"]
    assert selected == source_order[0]
    builders = job(args.handoff / f"memory/group-{selected}")
    matrix_root = args.handoff / f"continuations/group-{selected}"
    matrix = read(matrix_root / "admin-assigned-matrix.json")
    assert set(matrix["mapping"]) == set(science["sample"]["arms"])
    assigned = read(args.handoff / "assigned-future-opportunities.json")
    assert len(assigned) == 12 and {(row["arm"], row["traveler_index"]) for row in assigned} == {
        (arm, index) for arm in science["sample"]["arms"] for index in (3, 4)}
    futures, task_costs = [], []
    for arm in science["sample"]["arms"]:
        branch = matrix_root / matrix["mapping"][arm]
        req = job(branch)
        futures.extend(req)
        for row in (item for item in assigned if item["arm"] == arm):
            terminal = read(branch / "results" / (row["task_id"] + ".json"))
            task_requests = [request for request in req if request["logical_call_id"].split("/")[1] == row["task_id"]]
            task_costs.append({"arm": arm, "task_id": row["task_id"], "terminal_status": terminal["status"],
                               "rpc_seconds": duration(task_requests), "calls": len(task_requests)})
    all_phase = prefix_requests + builders + futures
    assert len({row["logical_call_id"] for row in all_phase}) == len(all_phase)

    def lifetime(folder, req):
        inspect = read(folder / "docker-inspect-final.json")[0]
        state = inspect["State"]
        assert state["Running"] is False and state["ExitCode"] == 0 and state["OOMKilled"] is False
        assert inspect["Config"]["User"].split(":")[0] not in {"", "0", "root"}
        started, finished = (datetime.fromisoformat(state[key]) for key in ("StartedAt", "FinishedAt"))
        first = min(datetime.fromisoformat(row["timestamp"]) for row in req)
        elapsed, startup = (finished - started).total_seconds(), (first - started).total_seconds()
        assert 0 <= startup < elapsed and duration(req) <= elapsed + 1
        return {"allocated_seconds": elapsed, "allocated_ceil_seconds": math.ceil(elapsed),
                "startup_before_first_request_seconds": startup, "post_startup_span_seconds": elapsed - startup,
                "rpc_seconds": duration(req)}

    prefix_time = lifetime(args.prefix, prefix_requests)
    handoff_time = lifetime(args.handoff, builders + futures)
    committed = [row["rpc_seconds"] for row in task_costs if row["terminal_status"] == "committed"]
    report = {"schema": "main_DEV_resource_calculation_v1", "created_at_utc": now.isoformat(),
              "science_plan_sha256": args.science_sha256, "permutation_file_sha256": args.permutation_sha256,
              "prefix": prefix_time, "handoff": handoff_time, "future_task_costs": task_costs,
              "memory_builder_calls": len(builders), "memory_builder_rpc_seconds": duration(builders),
              "prefix_model_calls": len(prefix_requests), "continuation_model_calls": len(futures),
              "new_model_calls": 0, "main_outcomes_opened": False,
              "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    assert len(builders) == 4 and committed, "Complete builder cost and at least one committed task are required by the fixed cost rule"
    upper_quartile = float(np.quantile(committed, 0.75, method="linear"))
    for row in task_costs:
        row["projected_rpc_seconds"] = row["rpc_seconds"] if row["terminal_status"] == "committed" else max(row["rpc_seconds"], upper_quartile)
    measured = prefix_time["post_startup_span_seconds"] / 2 + handoff_time["post_startup_span_seconds"]
    projected = duration(prefix_requests) / 2 + duration(builders) + sum(row["projected_rpc_seconds"] for row in task_costs)
    per_world = 1.25 * max(measured, projected)
    startup = prefix_time["startup_before_first_request_seconds"] + handoff_time["startup_before_first_request_seconds"]
    reserve = science["resources"]["source_audit_wall_buffer_seconds"]
    assert reserve == 10800
    available = (cutoff - now).total_seconds() - reserve - startup
    n = min(science["sample"]["desired_max_groups"], len(permutation["full_permutation"]), math.floor(available / per_world))
    assert 1 <= n <= 80, "No finite main sample fits the preregistered time window"
    operations = handoff_phase["operations"]
    report.update(status="finite_decision_ready", committed_task_upper_quartile_rpc_seconds=upper_quartile,
                  measured_per_world_span_seconds=measured, projected_per_world_rpc_seconds=projected,
                  margin_multiplier=1.25, conservative_per_world_seconds=per_world,
                  startup_allowance_seconds=startup, startup_accounting="One startup for each of the two planned model phases; never multiplied by N",
                  source_audit_wall_buffer_seconds=reserve, remaining_wall_seconds=(cutoff - now).total_seconds(),
                  available_per_world_budget_seconds=available, input_sha256=inputs)
    report["decision"] = {"schema": "main_finite_resource_decision_v1", "run_id": args.run_id, "N": n,
        "group_ids": permutation["full_permutation"][:n], "gpu_index_at_inspection": operations["gpu_index_at_inspection"],
        "hardware": {"host": operations["server"], "gpu_uuids": [operations["gpu_uuid"]], "gpu_names": [operations["gpu_name"]],
                     "tensor_parallel_size": 1, "endpoint_count": 1, "cpu_limit": 8, "RAM_limit_GiB": 64, "shm_GiB": 8},
        "budget": {"max_model_attempts": 144 * n, "max_input_output_tokens": 32768 * 144 * n,
                   "gpu_container_seconds": math.ceil(startup + n * per_world), "gpu_service_accounting": "sum_allocated_container_lifetimes",
                   "absolute_cutoff_utc": science["resources"]["absolute_collection_cutoff_utc"], "max_attempts_per_logical_request": 1,
                   "allocations": {"prefix_actors": 20 * n, "memory_builders": 4 * n, "continuation_actors": 120 * n}}}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": report["status"], "N": n, "resource_receipt_sha256": sha256(args.output.read_bytes()).hexdigest(),
                      "gpu_container_seconds": report["decision"]["budget"]["gpu_container_seconds"], "new_model_calls": 0}))


if __name__ == "__main__":
    main()
