"""Replay the H100 hardware/A/B prefix phase from immutable response caches.

Non-root CPU Docker only; no model client or network calls. Load the original
inference checkout explicitly. Memory and continuation phases need their own
replay profiles and are rejected here until those profiles are implemented.
"""

import argparse
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completed", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--inference-codepath", required=True, type=Path)
    parser.add_argument("--cycle", type=Path)
    parser.add_argument("--model", type=Path, default=Path("/model"))
    parser.add_argument("--catalog-csv", type=Path, default=Path("/database/accommodations/clean_accommodations_2022.csv"))
    args = parser.parse_args()
    if os.getuid() == 0 or not Path("/.dockerenv").exists():
        raise RuntimeError("Requires non-root CPU Docker")
    os.umask(0o077)
    completed, output, code = args.completed.resolve(), args.output.resolve(), args.inference_codepath.resolve()
    if output.exists() or output == completed or completed in output.parents:
        raise RuntimeError("Use a separate new audit output directory")
    sys.path.insert(0, str(code / "src"))

    import httpx
    from transformers import AutoTokenizer
    from collusion_handoff import abe_cycle, readiness_cycle
    from collusion_handoff.abe_protocol import Protocol
    from collusion_handoff.abe_runner import AdmissionFailure, BranchRunner, ServiceFailure
    from collusion_handoff.artifacts import atomic_json, canonical, fingerprint, read_json
    from collusion_handoff.catalog import load_accommodations
    from collusion_handoff.memory import strict_json
    from collusion_handoff.memory_v4 import constrained_payload

    class AuditMismatch(RuntimeError):
        pass

    def require(condition, message):
        if not condition:
            raise AuditMismatch(message)

    def rows(path):
        data = path.read_bytes() if path.exists() else b""
        require(not data or data.endswith(b"\n"), f"Truncated cache: {path}")
        return [strict_json(line) for line in data.decode().splitlines()]

    phase = read_json(completed / "phase-manifest.json")
    require(phase["phase"] == "hardware-and-development-prefix-v1", "Unimplemented replay profile; do not claim a pass")
    config = read_json(code / "configs/readiness-cycle-v5.json")
    base = read_json(code / config["base_runtime_config"])
    require(phase["config_sha256"] == sha256((code / "configs/readiness-cycle-v5.json").read_bytes()).hexdigest(), "Inference config changed")
    assets = {stage: {"prompt": (code / spec["prompt"]).read_text(), "schema": read_json(code / spec["schema"])}
              for stage, spec in config["memory"]["stages"].items()}
    require(fingerprint(assets) == phase["assets_sha256"], "Inference assets changed")
    require(config["limits"] == phase["limits"] and config["allocations"] == phase["allocations"], "Phase ceilings changed")
    require(config["operations"] == phase["operational_hardware"], "Hardware identity changed")
    output.mkdir(mode=0o700, parents=True)
    cycle = args.cycle.resolve() if args.cycle else completed.parent
    requests, responses = rows(cycle / "ledger/requests.jsonl"), rows(cycle / "ledger/responses.jsonl")
    by_request = {row["logical_call_id"]: row for row in requests}
    by_response = {row["logical_call_id"]: row for row in responses}
    require(len(by_request) == len(requests) and len(by_response) == len(responses), "Duplicate wire identity")
    require(set(by_response) <= set(by_request), "Orphan response")
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    allocations, tokens, unknown = {}, 0, []
    actor_sampler = abe_cycle.actor_generation(base)
    phase_ids = set()
    for row in requests:
        identity, allocation = row["logical_call_id"], row["allocation"]
        require(row["attempt"] == 1 and sha256(row["body_utf8"].encode()).hexdigest() == row["request_sha256"], "Wire hash or attempt changed")
        allocations[allocation] = allocations.get(allocation, 0) + 1
        receipt = by_response.get(identity)
        if receipt is None or receipt.get("accounted_actual_tokens") is None:
            unknown.append(identity)
            tokens += row["reserved_input_output_tokens"]
        else:
            require(receipt["request_sha256"] == row["request_sha256"], "Response is bound to another request")
            usage = strict_json(receipt["body_utf8"])["usage"]
            body = strict_json(row["body_utf8"])
            require(usage["prompt_tokens"] == row["rendered_input_tokens"]
                    and type(usage["completion_tokens"]) is int
                    and 0 <= usage["completion_tokens"] <= body["max_tokens"]
                    and receipt["accounted_actual_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"], "Raw usage does not match accounted tokens")
            tokens += receipt["accounted_actual_tokens"]
        if allocation in {"hardware_smoke", "developmental_prefixes"}:
            phase_ids.add(identity)
            body = strict_json(row["body_utf8"])
            require(all(body.get(key) == value for key, value in actor_sampler.items()), "Actor sampler changed")
            require(body["max_tokens"] == config["limits"]["actor_max_output_tokens"]
                    and body["response_format"]["type"] == "json_schema"
                    and body["response_format"]["json_schema"]["strict"] is True, "Actor delivery profile changed")
            prompt = tokenizer.apply_chat_template(body["messages"], tokenize=True, add_generation_prompt=True,
                                                   enable_thinking=False, return_dict=False)
            require(len(prompt) == row["rendered_input_tokens"] and len(prompt) + body["max_tokens"] <= config["limits"]["context"], "Actual rendered context differs")
    require(len(requests) <= config["limits"]["max_new_model_attempts"]
            and tokens <= config["limits"]["max_input_output_tokens"], "Global ceiling exceeded")
    require(all(key in config["allocations"] and count <= config["allocations"][key] for key, count in allocations.items()), "Allocation ceiling exceeded")

    consumed, results, errors = set(), {}, []

    def cached_response(body, logical_id, job):
        require(logical_id in by_request and logical_id not in consumed, "Missing or duplicate replay call")
        row = by_request[logical_id]
        require(strict_json(row["body_utf8"]) == body, "Reconstructed actual request differs from wire")
        require(row in rows(job / "io/requests.jsonl"), "Per-job request differs from global ledger")
        require(logical_id in by_response, "Unresolved response cannot be replayed as a success")
        response = by_response[logical_id]
        require(response in rows(job / "io/responses.jsonl"), "Per-job response differs from global ledger")
        consumed.add(logical_id)
        if response.get("transport_error"):
            raise AuditMismatch("Transport failure requires a separately reviewed failure replay profile")
        return httpx.Response(response["status_code"], content=response["body_utf8"].encode(),
                              request=httpx.Request("POST", "http://cache.invalid/no-network"))

    def check(name, operation):
        try:
            results[name] = operation()
        except Exception:
            errors.append({"check": name, "traceback": traceback.format_exc()})

    def hardware():
        replay = output / "hardware-replay"
        replay.mkdir()
        def dispatch(budget, body, logical_id, allocation, job):
            require(allocation == "hardware_smoke", "Unexpected probe allocation")
            return cached_response(body, logical_id, completed / "hardware-smoke")
        with patch.object(readiness_cycle, "dispatch", dispatch):
            abe_cycle.hardware_smoke(config, base, None, replay)
        for name in ("tool-receipt.json", "status.json"):
            require(read_json(replay / "hardware-smoke" / name) == read_json(completed / "hardware-smoke" / name), "Hardware receipt replay differs")
        return {"status": "passed", "new_model_calls": 0}

    check("hardware", hardware)
    catalog = load_accommodations(args.catalog_csv, config["task_policy"]["catalog_csv_sha256"])
    prompts = {role: (code / config["protocol"][role + "_prompt"]).read_text() for role in ("A", "B")}

    def prefix(number):
        job = completed / "prefix" / f"group-{number}"
        require((job / "status.json").exists(), "Assigned prefix has no terminal receipt")
        group, snapshot = read_json(job / "private-registry.json"), read_json(job / "snapshot.json")
        require(group["group_id"] == number, "Registry group changed")
        saved_results = {task["task_id"]: read_json(job / "results" / (task["task_id"] + ".json")) for task in group["tasks"][:2]}
        replayed_results = {}
        protocol = Protocol(number, abe_cycle.namespace(config["run_id"], number, "prefix"), catalog)
        private = {role: (code / config["protocol"]["private_" + role + "_prompt"]).read_text() for role in ("A", "B")} if number == 5 else {}

        def generate(role, messages, schema, logical_id, deadline, remaining):
            body = constrained_payload("qwen-native-dev", actor_sampler, messages, schema, config["limits"]["actor_max_output_tokens"])
            # Admission failures have no HTTP request; preserve the recorded
            # process boundary without inventing a cached completion.
            if logical_id not in by_request:
                failed = saved_results[protocol.task["task_id"]]
                detail = failed.get("failure") or {}
                if detail.get("type") == "AdmissionFailure":
                    raise AdmissionFailure(detail["message"], kind=detail["kind"], stop_phase=failed["status"] == "phase_stopped")
                raise AuditMismatch("No cached generation and no declared admission failure")
            response = cached_response(body, logical_id, job)
            text = readiness_cycle.content(response)
            usage = response.json()["usage"]
            require(usage["prompt_tokens"] + body["max_tokens"] <= remaining, "Replay would violate task reservation")
            return {"text": text, "input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"]}

        def accounting(logical_ids):
            selected = [by_request[identity] for identity in logical_ids if identity in by_request]
            missing = [row["logical_call_id"] for row in selected if by_response.get(row["logical_call_id"], {}).get("accounted_actual_tokens") is None]
            return {"attempts": len(selected), "accounted_input_output_tokens": sum(
                by_response.get(row["logical_call_id"], {}).get("accounted_actual_tokens")
                if by_response.get(row["logical_call_id"], {}).get("accounted_actual_tokens") is not None
                else row["reserved_input_output_tokens"] for row in selected), "unknown_spend_requests": missing}

        runner = BranchRunner(protocol, prompts, config["limits"], generate,
                              base=group["base_person"], private_goals=private,
                              persist=replayed_results.__setitem__, accounting=accounting)
        for task in group["tasks"][:2]:
            if saved_results[task["task_id"]]["status"] == "not_run_after_phase_failure":
                continue
            try:
                runner.run_task(task)
            except ServiceFailure:
                break
        for key, value in replayed_results.items():
            require(value == saved_results[key], "Prefix task evaluation/process receipt differs: " + key)
        for key in saved_results.keys() - replayed_results.keys():
            require(saved_results[key]["status"] == "not_run_after_phase_failure", "Assigned task silently omitted from replay")
        require(runner.histories == snapshot["histories"] and protocol.commits == snapshot["commits"]
                and protocol.events == snapshot["events"], "Replayed actor/world/events state differs")
        require(runner.requests == read_json(job / "actual-requests.json"), "Replayed actual message/origin requests differ")
        source = runner.source({"catalog_version": config["task_policy"]["catalog_csv_sha256"], "code_commit": phase["code_commit"]})
        require(source == read_json(job / "source.json"), "Source-policy projection differs")
        require(rows(job / "events.jsonl") == protocol.events, "Append-only on-disk events differ")
        atomic_json(output / f"prefix-{number}-results.json", replayed_results)
        return {"status": "passed", "tasks_replayed": len(replayed_results), "assigned_tasks": len(saved_results),
                "source_archive_sha256": source["archive_sha256"], "world_state_sha256": fingerprint(protocol.commits)}

    for number in (4, 5):
        check(f"prefix-{number}", lambda number=number: prefix(number))
    if consumed != phase_ids:
        errors.append({"check": "exact_cache_consumption", "unconsumed": sorted(phase_ids - consumed), "extra": sorted(consumed - phase_ids)})
    if unknown:
        errors.append({"check": "spend", "unknown_requests": unknown, "reason": "Unknown spend preserved; no complete accounting claim"})
    report = {"status": "passed_cache_only" if not errors else "failed", "phase": phase["phase"],
              "inference_commit": phase["code_commit"], "new_model_calls": 0, "no_HTTP_client_constructed": True,
              "all_v5_calls": len(requests), "phase_calls_replayed": len(consumed), "tokens": tokens,
              "allocations": allocations, "unknown_spend_requests": unknown, "checks": results, "errors": errors,
              "hardware": config["operations"], "parent_runs": config["parent_runs"], "main_started": False,
              "human_gold": False}
    atomic_json(output / "audit.json", report)
    print(canonical({"status": report["status"], "replayed_calls": len(consumed), "errors": len(errors), "new_model_calls": 0}).decode())
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
