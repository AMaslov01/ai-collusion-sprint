"""Read-only execution report and post-hoc admission diagnostics; no model client."""

from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path

from transformers import AutoTokenizer

from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.memory import require, strict_json
from collusion_handoff.memory_v3 import messages_v31


ROOT = Path("/cycle")


def rows(path):
    data = path.read_bytes() if path.exists() else b""
    require(not data or data.endswith(b"\n"), "Truncated durable ledger")
    return [strict_json(line) for line in data.decode().splitlines()]


def status(path):
    return read_json(path) if path.exists() else {"status": "not_run_or_interrupted"}


def main():
    os.umask(0o077)
    require(os.getuid() != 0, "Report requires non-root Docker")
    requests, responses = rows(ROOT / "ledger/requests.jsonl"), rows(ROOT / "ledger/responses.jsonl")
    require(len({item["logical_call_id"] for item in requests}) == len(requests) <= 80, "Repeated logical request or exceeded attempt ceiling")
    response_map = {item["logical_call_id"]: item for item in responses}
    allocations, tokens, unresolved = {}, 0, []
    profiles = {}
    for request in requests:
        allocation = request["allocation"]
        allocations[allocation] = allocations.get(allocation, 0) + 1
        require(sha256(request["body_utf8"].encode()).hexdigest() == request["request_sha256"], "Wire hash mismatch")
        body = strict_json(request["body_utf8"])
        require(body["n"] == 1 and body["chat_template_kwargs"]["enable_thinking"] is False, "Actual concurrency/thinking differs")
        expected_temperature = 0.7 if allocation.startswith("native_") else 0.0
        require(body["temperature"] == expected_temperature and body["seed"] == 42, "Actual sampler changed")
        profile = {key: body[key] for key in ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty", "seed", "n", "chat_template_kwargs")}
        category = "actor" if allocation.startswith("native_") else "builder"
        if category in profiles:
            require(profiles[category] == profile, "Profile differs within actor/builder category")
        profiles[category] = profile
        receipt = response_map.get(request["logical_call_id"])
        if receipt is not None and receipt.get("accounted_actual_tokens") is not None:
            tokens += receipt["accounted_actual_tokens"]
        else:
            tokens += request["reserved_input_output_tokens"]
            unresolved.append(request["logical_call_id"])
    require(tokens <= 3000000, "Global token ceiling exceeded")
    phase_names = ("regression-v1", "regression-v2", "heldout")
    phase_manifests = {name: read_json(ROOT / name / "phase-manifest.json") for name in phase_names}
    require(phase_manifests["regression-v2"]["assets_sha256"] == phase_manifests["heldout"]["assets_sha256"], "Assets tuned after final regression")
    require(phase_manifests["regression-v2"]["code_commit"] == phase_manifests["heldout"]["code_commit"], "Inference code changed after final regression")
    memory = {"regression-v1": status(ROOT / "regression-v1/memory/status.json"),
              "regression-v2": status(ROOT / "regression-v2/memory/status.json")}
    native = {}
    for group in (2, 3):
        job = ROOT / f"heldout/native/group-{group}"
        native[str(group)] = {"status": status(job / "status.json"), "cache_audit": status(job / "audit.json"),
                             "traveler_results": [status(job / f"results/group-{group}-traveler-{number}.json") for number in (1, 2)],
                             "third_fourth_generated": any((job / f"results/group-{group}-traveler-{number}.json").exists() for number in (3, 4))}
        require(not native[str(group)]["third_fourth_generated"], "Unapproved extra travelers")
        memory[f"group-{group}"] = status(ROOT / f"heldout/memory/group-{group}/status.json")
    diagnostics = {}
    tokens_model = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    for group in (2, 3):
        job = ROOT / f"heldout/memory/group-{group}"
        if (job / "source.json").exists() and "Fully rendered input overflow" in memory[f"group-{group}"].get("traceback", ""):
            source, assets = read_json(job / "source.json"), read_json(job / "assets.json")
            counts = {}
            for stage in ("S", "C"):
                messages = messages_v31(stage, source, [], assets[stage])
                ids = tokens_model.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False)
                require(isinstance(ids, list) and all(type(item) is int for item in ids), "Unexpected tokenizer return type")
                counts[stage] = len(ids)
            recorded = rows(job / "io/requests.jsonl")
            if recorded:
                require(recorded[0]["rendered_input_tokens"] == counts["S"], "CPU reconstruction differs from actual server S count")
            require(counts["C"] + 8192 > 32768, "Original C overflow not reproduced")
            require(not any(item["logical_call_id"].endswith("/C") for item in recorded), "C was unexpectedly dispatched")
            diagnostics[str(group)] = {"status": "input_overflow_reproduced_without_inference", "S_input_tokens": counts["S"],
                                      "C_input_tokens": counts["C"], "C_output_reserve": 8192, "context": 32768,
                                      "C_model_attempted": False, "new_model_calls": 0}
    fixture_definitions = read_json(Path("/code/fixtures/memory-semantic-v3.json"))["fixtures"]
    fixture_statuses = {item["fixture_id"]: status(ROOT / "heldout/fixtures" / item["fixture_id"] / "status.json") for item in fixture_definitions}
    resource = status(ROOT / "gpu-service-accounting.json")
    require(resource.get("total_allocated_gpu_container_seconds", 0) <= 7200, "GPU service allocation exceeded two hours")
    # Summaries contain process metadata/IDs, not semantic actor text or hidden gold answers.
    for group in native.values():
        group["traveler_results"] = [{key: value.get(key) for key in ("job_id", "parse_valid", "completion", "protocol_valid", "model_calls", "total_input_output_tokens")} for value in group["traveler_results"]]
    report = {"status": "completed_bounded_cycle_with_explicit_failures", "reported_at": datetime.now(timezone.utc).isoformat(),
              "scope": "diagnostic_development_and_two_fresh_DEV_examples_not_main", "new_model_calls_by_report": 0,
              "model_attempts": len(requests), "maximum_model_attempts": 80, "accounted_input_output_tokens": tokens,
              "maximum_input_output_tokens": 3000000, "allocations": allocations, "unknown_spend_requests": unresolved,
              "phase_manifests": phase_manifests, "actual_profiles": profiles, "native": native, "memory": memory,
              "admission_diagnostics": diagnostics, "semantic_fixtures": fixture_statuses, "resource_accounting": resource,
              "source_reference_origin": "Separate source-only AI diagnostics, not human gold", "human_completed_forms": 0,
              "semantic_quality_review": "Delegated to Diagnostics using frozen labels; process success is not main readiness",
              "fixture_e2e_domain": "Only predeclared reference-supported eligibility; off-support P outcomes unobserved",
              "main_started": False, "A_B_E_actor_experiment_started": False}
    atomic_json(Path("/work/report.json"), report)


if __name__ == "__main__":
    main()
