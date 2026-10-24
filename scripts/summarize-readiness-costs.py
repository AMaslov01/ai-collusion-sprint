"""CPU-only resource accounting and explicitly limited main workload estimates."""

import argparse
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import statistics


def read(path):
    return json.loads(path.read_text())


def seconds(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def describe(values):
    return {"count": len(values), "sum": sum(values), "mean": statistics.mean(values),
            "median": statistics.median(values), "minimum": min(values), "maximum": max(values)} if values else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.getuid() == 0 or not Path("/.dockerenv").exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise RuntimeError("Non-root CPU-only Docker required")
    if args.output.exists():
        raise RuntimeError("Do not replace an existing resource report")
    os.umask(0o077)
    config = read(args.config)
    files = [args.config, args.cycle / "ledger/requests.jsonl", args.cycle / "ledger/responses.jsonl"]
    rows = lambda p: [json.loads(line) for line in p.read_text().splitlines()]
    requests, responses = rows(files[1]), rows(files[2])
    by_response = {item["logical_call_id"]: item for item in responses}
    if len(by_response) != len(responses) or len({r["logical_call_id"] for r in requests}) != len(requests):
        raise RuntimeError("Duplicate accounting identity")
    categories, actor_tasks, checkpoint_memory = defaultdict(list), defaultdict(list), defaultdict(list)
    records = []
    for request in requests:
        identity = request["logical_call_id"]
        response = by_response.get(identity)
        if response is None or response.get("accounted_actual_tokens") is None:
            raise RuntimeError("Unknown spend prevents a complete cost estimate")
        body, raw = json.loads(request["body_utf8"]), json.loads(response["body_utf8"])
        usage = raw["usage"]
        if (sha256(request["body_utf8"].encode()).hexdigest() != request["request_sha256"]
                or response["request_sha256"] != request["request_sha256"]
                or usage["prompt_tokens"] != request["rendered_input_tokens"]
                or sum(usage[key] for key in ("prompt_tokens", "completion_tokens")) != response["accounted_actual_tokens"]):
            raise RuntimeError("Accounting differs from actual wire")
        allocation = request["allocation"]
        category = "hardware" if allocation == "hardware_smoke" else ("actor" if allocation.endswith(("prefixes", "actors")) else identity.rsplit("/", 1)[1])
        duration = response["duration_seconds"]
        record = {"logical_call_id": identity, "category": category, "allocation": allocation,
                  "timestamp": request["timestamp"], "duration_seconds": duration,
                  "input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"],
                  "total_tokens": response["accounted_actual_tokens"]}
        records.append(record)
        categories[category].append(record)
        match = re.search(r"/(g\d+-t\d+)/[AB]/", identity)
        if category == "actor" and match:
            actor_tasks[match.group(1)].append(record)
        if allocation == "developmental_memory" and "/checkpoint-group-" in identity:
            checkpoint_memory[identity.split("/")[-2]].append(record)

    def aggregate(items):
        elapsed = sum(item["duration_seconds"] for item in items)
        output = sum(item["output_tokens"] for item in items)
        return {"calls": len(items), "input_tokens": sum(item["input_tokens"] for item in items),
                "output_tokens": output, "total_tokens": sum(item["total_tokens"] for item in items),
                "request_duration_seconds": describe([item["duration_seconds"] for item in items]),
                "output_tokens_per_request_wall_second": output / elapsed if elapsed else None}

    inspected = {}
    for path in [*args.cycle.glob("resource-inspects/*.json"), *args.cycle.glob("*/docker-inspect.final.json")]:
        value = read(path)[0]
        labels = value["Config"]["Labels"]
        if labels.get("owner") == "a.maslov" and labels.get("scope") == config["run_id"].rsplit("-2026", 1)[0] and labels.get("computation") == "gpu":
            if value["State"]["Running"] or value["State"]["FinishedAt"].startswith("0001-"):
                raise RuntimeError("Allocated GPU container has no final receipt")
            inspected[value["Id"]] = (path, value)
    service = []
    for path, value in inspected.values():
        start, finish = seconds(value["State"]["StartedAt"]), seconds(value["State"]["FinishedAt"])
        selected = [item for item in records if start <= seconds(item["timestamp"]) <= finish]
        request_time = sum(item["duration_seconds"] for item in selected)
        service.append({"container": value["Name"], "inspection_file": str(path),
                        "started_at": value["State"]["StartedAt"], "finished_at": value["State"]["FinishedAt"],
                        "allocated_wall_seconds": finish - start, "allocated_ceil_seconds": math.ceil(finish - start),
                        "calls": len(selected), "request_seconds": request_time,
                        "startup_before_first_dispatch_seconds": min(seconds(item["timestamp"]) for item in selected) - start if selected else None,
                        "other_wall_seconds": finish - start - request_time})
        files.append(path)
    covered = [sum(seconds(item["started_at"]) <= seconds(record["timestamp"]) <= seconds(item["finished_at"])
                   for item in service) for record in records]
    if not service or any(count != 1 for count in covered):
        raise RuntimeError("GPU lifetime receipts do not cover all requests exactly")
    if not actor_tasks or len(checkpoint_memory) != 2:
        raise RuntimeError("Expected measured prefix tasks and both actual checkpoint memory jobs")
    task_costs = [aggregate(items) for items in actor_tasks.values()]
    memory_costs = [aggregate(items) for items in checkpoint_memory.values()]
    startup = max(item["startup_before_first_dispatch_seconds"] or 0 for item in service)
    limits = config["limits"]
    projections = {}
    for group_count in (40, 80):
        tasks_per_group = 2 + 6 * 2
        proxy_request_seconds = group_count * (tasks_per_group * statistics.mean(item["request_duration_seconds"]["sum"] for item in task_costs)
                                                + statistics.mean(item["request_duration_seconds"]["sum"] for item in memory_costs))
        calls_per_group = tasks_per_group * limits["task_max_calls"] + 4
        projections[str(group_count)] = {
            "independent_groups": group_count, "prefix_tasks_per_group": 2,
            "continuation_tasks_per_group": 12, "memory_build_calls_per_group": 4,
            "prefix_only_actor_proxy_calls": group_count * (tasks_per_group * statistics.mean(item["calls"] for item in task_costs) + 4),
            "prefix_only_actor_proxy_input_output_tokens": group_count * (tasks_per_group * statistics.mean(item["total_tokens"] for item in task_costs)
                                                                           + statistics.mean(item["total_tokens"] for item in memory_costs)),
            "prefix_only_actor_proxy_request_seconds": proxy_request_seconds,
            "prefix_only_actor_proxy_one_startup_hours": (proxy_request_seconds + startup) / 3600,
            "largest_observed_prefix_task_and_memory_proxy_hours": (group_count * (tasks_per_group * max(item["request_duration_seconds"]["sum"] for item in task_costs)
                                                                                  + max(item["request_duration_seconds"]["sum"] for item in memory_costs)) + startup) / 3600,
            "finite_max_model_calls_excluding_hardware": group_count * calls_per_group,
            "finite_max_input_output_tokens_excluding_hardware": group_count * (tasks_per_group * limits["task_max_input_output_tokens"] + 4 * limits["context"]),
            "finite_task_and_builder_timeout_hours_excluding_startup": group_count * (tasks_per_group * limits["task_wall_seconds"] + 4 * limits["request_timeout_seconds"]) / 3600,
            "approved_main_budget": False}
    report = {"status": "complete_measured_costs_with_limited_resource_projections", "run_id": config["run_id"],
              "new_model_calls": 0, "paid_API_USD": 0, "unknown_spend_requests": [],
              "categories": {key: aggregate(value) for key, value in categories.items()},
              "observed_actor_tasks": {key: aggregate(value) for key, value in actor_tasks.items()},
              "actual_checkpoint_memory": {key: aggregate(value) for key, value in checkpoint_memory.items()},
              "gpu_service": service, "allocated_gpu_ceil_seconds": sum(item["allocated_ceil_seconds"] for item in service),
              "projections": projections,
              "projection_limitations": ["Only four developmental prefix tasks and two actual A/B memory jobs were measured.",
                  "No continuation actor latency has been measured; longer histories and final context admission can change cost substantially.",
                  "Invalid proposals and refusal tasks are retained in the prefix proxy; they can make it optimistic for completed negotiations.",
                  "A single observed startup is added to the proxy; annotation, CPU audits, waiting for GPU, and further service starts are excluded.",
                  "Finite per-task ceilings do not authorize or replace a global main GPU/time/token/call budget."],
              "input_sha256": {str(path): sha256(path.read_bytes()).hexdigest() for path in files}}
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"calls": len(requests), "tokens": sum(item["total_tokens"] for item in records),
                      "allocated_gpu_ceil_seconds": report["allocated_gpu_ceil_seconds"], "new_model_calls": 0}))


if __name__ == "__main__":
    main()
