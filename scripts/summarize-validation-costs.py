"""Closed L40S DEV costs; empirical workload projections are not main authority."""

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def seconds(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def aggregate(records):
    known = [r for r in records if r["total_tokens"] is not None]
    return {"calls": len(records), "known_input_output_tokens": sum(r["total_tokens"] for r in known),
            "unknown_spend_calls": len(records) - len(known),
            "input_tokens": sum(r["input_tokens"] for r in known),
            "output_tokens": sum(r["output_tokens"] for r in known),
            "request_wall_seconds": sum(r["duration_seconds"] or 0 for r in records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups", type=int, nargs="+", default=[6, 7])
    parser.add_argument("--project-groups", type=int, nargs="+", default=[40, 80])
    args = parser.parse_args()
    require(os.getuid() != 0 and Path("/.dockerenv").exists()
            and os.environ.get("CUDA_VISIBLE_DEVICES") == "", "Non-root CPU-only Docker required")
    require(not args.output.exists(), "Never replace a resource report")
    require(len(set(args.groups)) == len(args.groups) and all(g > 0 for g in args.groups), "Invalid observed groups")
    require(all(n > 0 for n in args.project_groups), "Invalid projection size")
    os.umask(0o077)
    inputs = {args.config}
    config = read(args.config)
    phases = [args.cycle / name for name in ("validation-prefix-v1", "validation-continuations-v1")]
    manifests, lifetimes, identities = [], [], {}
    for phase in phases:
        manifest_path, inspect_path = phase / "phase-manifest.json", phase / "docker-inspect.final.json"
        manifest, inspection = read(manifest_path), read(inspect_path)[0]
        inputs.update((manifest_path, inspect_path))
        state, labels = inspection["State"], inspection["Config"]["Labels"]
        require(not state["Running"] and state["Status"] == "exited" and state["ExitCode"] == 0
                and not state["OOMKilled"], "Phase is not successfully closed")
        require(labels.get("owner") == "a.maslov" and labels.get("computation") == "gpu"
                and labels.get("adapter_commit") == manifest["code_commit"], "Unbound GPU receipt")
        device_ids = [device for request in inspection["HostConfig"]["DeviceRequests"] for device in request.get("DeviceIDs", [])]
        require(device_ids == [manifest["operational_hardware"]["gpu_uuid"]], "Actual GPU allocation differs from manifest")
        require(manifest["config_sha256"] == sha256(args.config.read_bytes()).hexdigest(), "Config differs from inference")
        start, finish = seconds(state["StartedAt"]), seconds(state["FinishedAt"])
        require(finish > start, "Invalid GPU lifetime")
        manifests.append(manifest)
        lifetimes.append({"phase": phase.name, "container_id": inspection["Id"],
                          "started_at": state["StartedAt"], "finished_at": state["FinishedAt"],
                          "allocated_seconds": finish - start, "allocated_ceil_seconds": math.ceil(finish - start)})
        for path in phase.glob("**/io/requests.jsonl"):
            inputs.add(path)
            for request in rows(path):
                identity = request["logical_call_id"]
                require(identity not in identities, "Duplicate phase call identity")
                identities[identity] = {"phase": phase.name, "io_request": request}
    for key in ("code_commit", "config_sha256", "assets_sha256", "model", "image_digest", "operational_hardware"):
        require(manifests[0][key] == manifests[1][key], "Different L40S inference conditions")
    require(manifests[0]["operational_hardware"]["gpu_uuid"] == "GPU-c15d4e7a-29b2-896a-f7bf-3d1a528040a5", "Unexpected hardware stratum")
    requests_path, responses_path = args.cycle / "ledger/requests.jsonl", args.cycle / "ledger/responses.jsonl"
    inputs.update((requests_path, responses_path))
    requests, responses = rows(requests_path), rows(responses_path)
    response_by_id = {r["logical_call_id"]: r for r in responses}
    require(len(response_by_id) == len(responses) and len({r["logical_call_id"] for r in requests}) == len(requests), "Duplicate ledger identity")
    records = []
    for request in requests:
        identity = request["logical_call_id"]
        if identity not in identities:
            continue
        require(request == identities[identity]["io_request"], "Phase IO differs from master ledger")
        require(sha256(request["body_utf8"].encode()).hexdigest() == request["request_sha256"], "Request wire hash mismatch")
        response = response_by_id.get(identity)
        usage, actual = None, None
        if response is not None:
            require(response["request_sha256"] == request["request_sha256"], "Response binding mismatch")
            actual = response.get("accounted_actual_tokens")
            if actual is not None:
                usage = json.loads(response["body_utf8"])["usage"]
                require(usage["prompt_tokens"] == request["rendered_input_tokens"]
                        and usage["prompt_tokens"] + usage["completion_tokens"] == actual, "Usage mismatch")
        record = {"logical_call_id": identity, "phase": identities[identity]["phase"],
                  "timestamp": request["timestamp"], "allocation": request["allocation"],
                  "total_tokens": actual, "input_tokens": usage["prompt_tokens"] if usage else None,
                  "output_tokens": usage["completion_tokens"] if usage else None,
                  "duration_seconds": response.get("duration_seconds") if response else None}
        covered = [p for p in lifetimes if seconds(p["started_at"]) <= seconds(record["timestamp"]) <= seconds(p["finished_at"])]
        require(len(covered) == 1 and covered[0]["phase"] == record["phase"], "Call not covered by its GPU lifetime")
        records.append(record)
    require(len(records) == len(identities), "Phase request missing from master ledger")
    observed_path = phases[1] / "all-assigned-results.json"
    inputs.add(observed_path)
    observed = read(observed_path)
    arms = ("F", "S", "C", "CV", "CVP", "R")
    expected = {(g, arm, f"g{g}-t{task}") for g in args.groups for arm in arms for task in (3, 4)}
    require(observed["assigned"] == len(expected) and
            {(r["group_id"], r["arm"], r["task_id"]) for r in observed["observed"]} == expected
            and len(observed["observed"]) == len(expected), "All assigned future opportunities are required")
    arm_by_session = {}
    for group in args.groups:
        path = phases[1] / "continuations" / f"group-{group}" / "admin-assigned-matrix.json"
        inputs.add(path)
        for arm, session in read(path)["mapping"].items():
            require(session not in arm_by_session, "Duplicate branch session")
            arm_by_session[session] = (group, arm)
    prefix, builders, actors = {}, {}, {}
    for group in args.groups:
        prefix[group] = [r for r in records if r["phase"] == phases[0].name and re.search(rf"/g{group}-t[12]/[AB]/", r["logical_call_id"])]
        builders[group] = {stage: [r for r in records if r["logical_call_id"] == f"{phases[1].name}/group-{group}/{stage}"] for stage in ("S", "C", "V", "P")}
        require(all(len(v) == 1 for v in builders[group].values()), "Expected one attempt for each actual builder")
        require({re.search(r"/(g\d+-t\d+)/", r["logical_call_id"]).group(1) for r in prefix[group]} == {f"g{group}-t1", f"g{group}-t2"}, "Both prefix tasks required")
        actors[group] = {arm: [r for r in records if arm_by_session.get(r["logical_call_id"].split("/")[0]) == (group, arm)] for arm in arms}
    dependencies = {"F": (), "R": (), "S": ("S",), "C": ("C",), "CV": ("C", "V"), "CVP": ("C", "V", "P")}
    method_costs = {arm: aggregate([r for g in args.groups for r in actors[g][arm]] +
                                  [r for g in args.groups for stage in dependencies[arm] for r in builders[g][stage]]) for arm in arms}
    scientific = [r for g in args.groups for r in prefix[g]] + [r for g in args.groups for arm in arms for r in actors[g][arm]] + [r for g in args.groups for stage in ("S", "C", "V", "P") for r in builders[g][stage]]
    require(len({r["logical_call_id"] for r in scientific}) == len(scientific), "Scientific costs double counted")
    unknown = [r["logical_call_id"] for r in records if r["total_tokens"] is None]
    for p in lifetimes:
        selected = [r for r in records if r["phase"] == p["phase"]]
        p["requests"] = aggregate(selected)
        p["startup_before_first_request_seconds"] = min(seconds(r["timestamp"]) for r in selected) - seconds(p["started_at"])
        p["other_wall_seconds"] = p["allocated_seconds"] - p["requests"]["request_wall_seconds"]
    projections = None
    if not unknown:
        measured = aggregate(scientific)
        startup = max(p["startup_before_first_request_seconds"] for p in lifetimes)
        count = len(args.groups)
        limits = config["limits"]
        projections = {str(n): {"independent_groups": n, "source_groups_measured": count,
            "empirical_model_attempts": n * measured["calls"] / count,
            "empirical_input_output_tokens": n * measured["known_input_output_tokens"] / count,
            "empirical_request_seconds": n * measured["request_wall_seconds"] / count,
            "empirical_one_startup_hours": (n * measured["request_wall_seconds"] / count + startup) / 3600,
            "empirical_two_startups_hours": (n * measured["request_wall_seconds"] / count + 2 * startup) / 3600,
            "finite_max_attempts_excluding_smoke": n * (14 * limits["task_max_calls"] + 4),
            "finite_max_input_output_tokens": n * (14 * limits["task_max_input_output_tokens"] + 4 * limits["context"]),
            "finite_task_and_builder_timeout_hours_excluding_startup": n * (14 * limits["task_wall_seconds"] + 4 * limits["request_timeout_seconds"]) / 3600,
            "approved_main_budget": False} for n in args.project_groups}
    report = {"schema": "closed_l40s_validation_costs_v1", "new_model_calls": 0,
        "status": "measured_with_limited_empirical_projections" if not unknown else "unknown_spend_no_projection",
        "inference_binding": {k: manifests[0][k] for k in ("code_commit", "config_sha256", "assets_sha256", "model", "image_digest", "operational_hardware")},
        "observed_groups": args.groups, "all_assigned_statuses": dict(Counter(r["result"]["status"] for r in observed["observed"])),
        "unknown_spend_requests": unknown, "selected_l40s_all_calls": aggregate(records),
        "scientific_experiment_unique_calls": aggregate(scientific), "method_builder_plus_actor_costs": method_costs,
        "gpu_lifetimes": lifetimes, "allocated_l40s_ceil_seconds": sum(p["allocated_ceil_seconds"] for p in lifetimes),
        "global_master_calls_including_prior_h100": len(requests),
        "global_master_known_tokens": sum(r.get("accounted_actual_tokens") or 0 for r in responses),
        "global_master_unknown_spend": [r["logical_call_id"] for r in requests if r["logical_call_id"] not in response_by_id or response_by_id[r["logical_call_id"]].get("accounted_actual_tokens") is None],
        "projections": projections,
        "limitations": ["Small DEV sample with formal invalids can substantially understate cost of successful multi-turn execution.",
            "H100 calls are in cumulative accounting only, never pooled into L40S empirical costs.",
            "Method attribution includes required builders once per source, not all builders for every arm; its sum intentionally duplicates shared C/V stages.",
            "HTTP durations depend on fixed arm order and shared warm caches; these are not standalone causal latency effects.",
            "Resource projections extrapolate observed prefix+future+builder workload, not a power calculation or main sample/budget authorization.",
            "Empirical hours exclude CPU audits, annotation, queueing and shutdown overhead; finite ceilings are separate from empirical means."],
        "input_sha256": {str(p): sha256(p.read_bytes()).hexdigest() for p in sorted(inputs)}}
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "selected_calls": len(records), "new_model_calls": 0}))


if __name__ == "__main__":
    main()
