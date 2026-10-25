"""Cache-only replay of frozen v4 components or v5 checkpoint/final-DEV memory.

Run in non-root CPU Docker with the completed run mounted read-only. This script
constructs no HTTP client and never generates responses. Inference code is loaded
from the explicitly supplied immutable checkout, separate from this audit code.
"""

import argparse
from hashlib import sha256
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inference-codepath", type=Path, default=Path("/code"))
    parser.add_argument("--cycle", type=Path)
    parser.add_argument("--model", type=Path, default=Path("/model"))
    parser.add_argument("--calibration-contract", type=Path)
    parser.add_argument("--calibration-review", type=Path)
    parser.add_argument("--calibration-preflight", type=Path)
    args = parser.parse_args()
    if os.getuid() == 0 or not Path("/.dockerenv").exists():
        raise RuntimeError("Requires the approved non-root CPU Docker")
    os.umask(0o077)
    completed, output = args.completed.resolve(), args.output.resolve()
    code = args.inference_codepath.resolve()
    if completed == output or completed in output.parents:
        raise RuntimeError("Audit output must be separate from immutable completed artifacts")
    if output.exists():
        raise RuntimeError("Use a new immutable audit output directory")
    sys.path.insert(0, str(code / "src"))

    import httpx
    from transformers import AutoTokenizer
    from collusion_handoff.artifacts import atomic_json, canonical, fingerprint, read_json
    from collusion_handoff.memory import apply_verdicts, build_packages, require as runtime_require, strict_json
    from collusion_handoff.memory_v3 import fixture_eligible, fixture_mapping, fixture_source
    from collusion_handoff.memory_v4 import (
        constrained_payload, decode_v4, full_delivery_v4, messages_v4,
        record_packer, resolve_v4,
    )
    from collusion_handoff.readiness_cycle import content

    class AuditMismatch(RuntimeError):
        pass

    def require(condition, message):
        if not condition:
            raise AuditMismatch(message)

    output.mkdir(mode=0o700, parents=True)
    phase = read_json(completed / "phase-manifest.json")
    profiles = {"development-memory-v1": "configs/readiness-cycle-v4.json",
                "development-checkpoint-memory-v1": "configs/readiness-cycle-v5.json",
                "validation-continuations-v1": "configs/readiness-cycle-v5.json"}
    calibration = args.calibration_contract is not None
    cycle = args.cycle.resolve() if args.cycle else completed.parent
    require(calibration or phase["phase"] in profiles, "Unimplemented replay profile")
    if calibration:
        from collusion_handoff import calibration_cycle
        require(args.calibration_review and args.calibration_preflight and os.environ.get("CUDA_VISIBLE_DEVICES") == "" and
                not list(Path("/dev").glob("nvidia[0-9]*")) and {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"},
                "Calibration component replay requires explicit CPU-only review/preflight inputs")
        config_path = args.calibration_contract.resolve()
        require(config_path.is_relative_to(code), "Calibration contract is outside the frozen inference checkout")
        with patch.object(calibration_cycle, "CODE", code), patch.object(calibration_cycle, "ROOT", cycle), \
             patch.object(calibration_cycle, "REVIEW", args.calibration_review.resolve()), \
             patch.object(calibration_cycle, "CONFIG", str(config_path.relative_to(code))), \
             patch.dict(os.environ, {"ADAPTER_COMMIT": phase["code_commit"], "CYCLE_PHASE": phase["phase"]}):
            amendment, config, _, effective_assets, closure = calibration_cycle.settings()
            require(phase["phase"] == amendment["phases"]["handoff"], "Calibration memory replay requires the handoff phase")
            require(phase == calibration_cycle.phase_identity(amendment, config, read_json(code / config["base_runtime_config"]), effective_assets),
                    "Calibration phase differs from the frozen effective identity")
        pre = read_json(args.calibration_preflight / "preflight.json")
        require(pre["status"] == "passed_no_model_calls" and pre["identity"] == phase, "Actual calibration preflight differs")
        calibration_cycle.verify_preflight_inventory(args.calibration_preflight, pre)
        calibration_cycle.verify_review_inputs(args.calibration_review, pre)
        require(type(pre["handoff_world"]) is int and pre["handoff_world"] in pre["selected_worlds"], "No selected calibration source")
        require(read_json(completed / "source-selection.json") == read_json(args.calibration_preflight / "source-selection.json"),
                "Actual calibration selection changed")
        prefix_root = cycle / amendment["run_id"] / amendment["phases"]["prefix"]
        require(calibration_cycle.prefix_bundle(prefix_root, pre["selected_worlds"]) == pre["historical_prefix_bundle"],
                "Frozen historical calibration B/world/source inputs changed")
    else:
        config_path = code / profiles[phase["phase"]]
        config = read_json(config_path)
    base = read_json(code / config["base_runtime_config"])
    if phase["phase"] != "development-memory-v1":
        from collusion_handoff.readiness_cycle import memory_implementation
        message_builder, resolver, full_delivery = memory_implementation(config)
    else:
        message_builder, resolver, full_delivery = messages_v4, resolve_v4, full_delivery_v4
    assets = read_json(completed / "assets.json")
    phase_name = phase["phase"]
    errors, results, checks = [], {}, {}

    def read_rows(path):
        data = path.read_bytes() if path.exists() else b""
        require(not data or data.endswith(b"\n"), f"Truncated ledger tail: {path}")
        return [strict_json(line) for line in data.decode().splitlines()]

    def count_input(body):
        return len(tokenizer.apply_chat_template(
            body["messages"], tokenize=True, add_generation_prompt=True,
            enable_thinking=False, return_dict=False))

    def expected_payload(stage, messages):
        return constrained_payload("qwen-native-dev", config["memory"]["generation"], messages,
                                   assets[stage]["schema"], config["memory"]["stages"][stage]["max_output_tokens"])

    def guard(name, operation):
        try:
            checks[name] = operation()
        except Exception:
            errors.append({"check": name, "traceback": traceback.format_exc()})

    def identities():
        require(calibration or phase["phase"] in profiles, "Unsupported phase profile")
        require(sha256(config_path.read_bytes()).hexdigest() == phase["config_sha256"],
                "Inference config differs from phase fingerprint")
        actual_assets = {stage: {"prompt": (code / spec["prompt"]).read_text(),
                                "schema": read_json(code / spec["schema"])}
                         for stage, spec in config["memory"]["stages"].items()}
        require(actual_assets == assets and fingerprint(assets) == phase["assets_sha256"],
                "Inference assets differ from frozen phase")
        require(config["limits"] == phase["global_limits" if calibration else "limits"] and
                config["allocations"] == phase["global_allocations" if calibration else "allocations"],
                "Limits/allocations changed")
        require(config["paid_api_budget_usd"] == 0, "Unexpected paid route")
        return {"status": "passed", "inference_commit": phase["code_commit"],
                "assets_sha256": fingerprint(assets), "config_sha256": phase["config_sha256"]}

    guard("identity", identities)
    if errors:
        atomic_json(output / "audit.json", {"status": "failed", "errors": errors, "new_model_calls": 0})
        raise SystemExit(1)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    requests, responses = read_rows(cycle / "ledger/requests.jsonl"), read_rows(cycle / "ledger/responses.jsonl")
    request_map = {row["logical_call_id"]: row for row in requests}
    response_map = {row["logical_call_id"]: row for row in responses}
    consumed = set()

    def ledger_audit():
        require(len(request_map) == len(requests) <= config["limits"]["max_new_model_attempts"],
                "Duplicate logical request or exceeded attempt ceiling")
        require(len(response_map) == len(responses) and set(response_map) <= set(request_map),
                "Duplicate/orphan response receipt")
        allocations, total, unknown = {}, 0, []
        for request in requests:
            key = request["logical_call_id"]
            allocations[request["allocation"]] = allocations.get(request["allocation"], 0) + 1
            require(sha256(request["body_utf8"].encode()).hexdigest() == request["request_sha256"]
                    and request["attempt"] == 1, "Wire hash/attempt changed")
            body = strict_json(request["body_utf8"])
            require(body["n"] == 1 and body["stream"] is False and body["chat_template_kwargs"]["enable_thinking"] is False,
                    "Actual request multiplicity/thinking changed")
            if key.startswith(phase_name + "/"):
                expected_generation = config["memory"]["generation"]
                require(all(body.get(name) == value for name, value in expected_generation.items()), "Builder sampler changed")
                require(body["response_format"]["type"] == "json_schema"
                        and body["response_format"]["json_schema"]["strict"] is True,
                        "Actual constrained delivery missing")
                rendered = count_input(body)
                require(rendered == request["rendered_input_tokens"], "Rendered prompt-token count mismatch")
                require(rendered + body["max_tokens"] <= config["limits"]["context"], "Dispatched input overflow")
            receipt = response_map.get(key)
            if receipt is None or receipt.get("accounted_actual_tokens") is None:
                unknown.append(key)
                total += request["reserved_input_output_tokens"]
            else:
                require(receipt["request_sha256"] == request["request_sha256"], "Response bound to different request")
                raw = strict_json(receipt["body_utf8"])
                usage = raw["usage"]
                require(usage["prompt_tokens"] == request["rendered_input_tokens"]
                        and type(usage["completion_tokens"]) is int
                        and 0 <= usage["completion_tokens"] <= body["max_tokens"]
                        and receipt["accounted_actual_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"],
                        "Accounted usage differs from raw receipt")
                total += receipt["accounted_actual_tokens"]
        require(total <= config["limits"]["max_input_output_tokens"], "Token ceiling exceeded")
        require(all(key in config["allocations"] and count <= config["allocations"][key]
                    for key, count in allocations.items()), "Allocation ceiling exceeded")
        require(not unknown, "Unresolved spend; successful complete accounting cannot be claimed")
        return {"status": "passed", "all_block_calls": len(requests), "tokens": total,
                "allocations": allocations, "unknown_spend": unknown,
                "requests_sha256": sha256((cycle / "ledger/requests.jsonl").read_bytes()).hexdigest(),
                "responses_sha256": sha256((cycle / "ledger/responses.jsonl").read_bytes()).hexdigest()}

    guard("ledger", ledger_audit)

    def replay_call(request, response, expected):
        key = request["logical_call_id"]
        require(key not in consumed, "Replayed a cached request twice")
        require(key in request_map and request_map[key] == request, "Job request differs from global ledger")
        require(key in response_map and response_map[key] == response, "Job response differs from global ledger")
        require(strict_json(request["body_utf8"]) == expected, "Reconstructed payload differs from actual wire")
        consumed.add(key)
        require(response.get("status_code") is not None, "Cached transport failure boundary")
        return content(httpx.Response(response["status_code"], content=response["body_utf8"].encode(),
                                      request=httpx.Request("POST", "http://cache.invalid/no-network")))

    def replay_memory(job):
        source = read_json(job / "source.json")
        require(fingerprint(source["archive"]) == source["archive_sha256"], "Source archive changed")
        require(read_json(job / "assets.json") == assets, "Job assets changed")
        req, resp = read_rows(job / "io/requests.jsonl"), read_rows(job / "io/responses.jsonl")
        require(len(req) == len(resp), "Unresolved memory request")
        cursor, replayed = 0, {}
        summary = None

        def cached(stage, messages):
            nonlocal cursor
            body = expected_payload(stage, messages)
            runtime_require(count_input(body) + body["max_tokens"] <= config["limits"]["context"],
                            "Fully rendered input overflow; preserved pre-send failure")
            require(cursor < len(req), "No cached next attempt; never generate a replacement")
            require(req[cursor]["logical_call_id"].endswith("/" + stage), "Stage order differs")
            request, response = req[cursor], resp[cursor]
            cursor += 1
            return replay_call(request, response, body)

        failure = None
        try:
            summary = build_packages(source, {"packing": {"archive_wrapper": config["memory"]["archive_wrapper"]}}, assets,
                           cached, lambda text: len(tokenizer.encode(text, add_special_tokens=False)),
                           replayed.__setitem__, decode=decode_v4, resolve=resolver,
                           full_archive_content=full_delivery, message_builder=message_builder,
                           record_packer=record_packer(source["archive"]))
        except Exception as error:
            if isinstance(error, AuditMismatch):
                raise
            failure = {"type": type(error).__name__, "message": str(error)}
        actual = read_json(job / "status.json")
        require((failure is None) == (actual["status"] == "built"), "Memory success/failure differs on replay")
        require(cursor == len(req), "Not every actual memory call replayed")
        if failure is None:
            require(actual["summary"] == summary, "Memory package summary differs")
        else:
            require(actual["failure_type"] == failure["type"], "Memory failure type differs")
        stored = {str(path.relative_to(job / "automatic-withheld"))
                  for path in (job / "automatic-withheld").rglob("*.json")}
        require(stored == set(replayed), "Replay artifact set differs")
        for name, value in replayed.items():
            require(read_json(job / "automatic-withheld" / name) == value, f"Replayed artifact differs: {name}")
            atomic_json(output / "replayed" / job.name / name, value)
        return {"status": "passed_replay", "actual_job_status": actual["status"], "cached_calls": cursor,
                "artifacts_compared": len(replayed), "preserved_failure": failure}

    def replay_fixture(definition, job):
        if phase_name == "development-memory-v1":
            source, candidates = fixture_source(definition)
        else:
            from collusion_handoff.readiness_cycle import fixture_input
            source, candidates = fixture_input(config, definition)
        require(read_json(job / "source.json") == source and read_json(job / "candidates.json") == candidates,
                "Controlled inputs changed")
        require(read_json(job / "audit-mapping.json") == fixture_mapping(definition, source, candidates),
                "Fixture mapping changed")
        req, resp = read_rows(job / "io/requests.jsonl"), read_rows(job / "io/responses.jsonl")
        require(len(req) == len(resp), "Unresolved fixture request")
        cursor, failures, chosen = 0, {}, {}
        eligible = fixture_eligible(definition, candidates)
        for stage, selected in (("V", candidates), ("P", eligible)):
            try:
                messages = message_builder(stage, source, selected, assets[stage])
                body = expected_payload(stage, messages)
                runtime_require(count_input(body) + body["max_tokens"] <= config["limits"]["context"], "Fixture admission overflow")
                require(cursor < len(req) and req[cursor]["logical_call_id"].endswith("/" + stage),
                        "Missing cached fixture stage; no replacement")
                request, response = req[cursor], resp[cursor]
                cursor += 1
                value = decode_v4(stage, replay_call(request, response, body), assets[stage]["schema"])
                require(read_json(job / "automatic-withheld/stages" / f"{stage}.json") == value, "Fixture output differs")
                retained, _ = apply_verdicts(selected, value["verdicts"], field="verdict" if stage == "V" else "label",
                                            retain="supported" if stage == "V" else "allow")
                chosen[stage] = {claim["claim_id"] for claim in retained}
            except Exception as error:
                if isinstance(error, AuditMismatch):
                    raise
                failures[stage] = {"type": type(error).__name__, "message": str(error)}
        actual = read_json(job / "status.json")
        require(set(actual["component_failures"]) == set(failures), "Fixture failed stages differ")
        require(all(actual["component_failures"][stage]["type"] == failure["type"]
                    for stage, failure in failures.items()), "Fixture failure types differ")
        require((not failures) == (actual["status"] == "complete"), "Fixture terminal status differs")
        require(cursor == len(req), "Not every fixture request replayed")
        require(actual["component_p_eligible_ids"] == sorted(claim["claim_id"] for claim in eligible), "P domain differs")
        for stage, field in (("V", "actual_v_supported_ids"), ("P", "component_p_allowed_ids")):
            require(actual[field] == (sorted(chosen[stage]) if stage in chosen else None), "Fixture selected IDs differ")
        return {"status": "passed_replay", "cached_calls": cursor, "preserved_component_failures": failures}

    def jobs_audit():
        require((completed / "phase-status.json").exists(), "Component did not reach a complete receipt")
        terminal = read_json(completed / "phase-status.json")
        if calibration:
            number = pre["handoff_world"]
            key = f"group-{number}"
            job = completed / "memory" / key
            source = read_json(prefix_root / "prefix" / key / "source.json")
            require(read_json(job / "source.json") == source, "Memory input differs from the locked actual source")
            require(read_json(job / "manifest.json") == {"job_id": phase_name + "/" + key,
                    "source_archive_sha256": source["archive_sha256"], "assets_sha256": fingerprint(assets)},
                    "Selected memory-job identity differs")
            results[key] = replay_memory(job)
            actual_ids = {identity for identity in request_map if identity.startswith(phase_name + "/")}
            require(consumed == actual_ids and len(consumed) <= amendment["child_limits"]["memory_builder_calls"],
                    "Unconsumed, extra or over-budget calibration builder requests")
            require(all(request_map[identity]["allocation"] == "reviewed_targeted_repairs" for identity in consumed),
                    "Calibration builder charged another allocation")
            return {"status": "passed", "jobs": 1, "exact_component_calls_consumed": len(consumed),
                    "actor_continuation_replay": "Separate required audit; not covered by this component profile"}
        if phase_name == "development-checkpoint-memory-v1":
            planned = {"known-regression-group-1", "checkpoint-group-4", "checkpoint-group-5"}
            require(set(terminal["jobs"]) == planned, "Missing assigned v5 memory job status")
            for key in sorted(planned):
                job = completed / "memory" / key
                results[key] = replay_memory(job)
                require(read_json(job / "status.json") == terminal["jobs"][key], "Phase/job status mismatch")
            actual_phase_ids = {key for key in request_map if key.startswith(phase_name + "/")}
            require(consumed == actual_phase_ids, "Unconsumed or unexpected checkpoint memory calls")
            require(len(consumed) <= 12, "V5 checkpoint memory call ceiling exceeded")
            return {"status": "passed", "jobs": len(results), "exact_phase_calls_consumed": len(consumed)}
        if phase_name == "validation-continuations-v1":
            from collusion_handoff.readiness_cycle import fresh_definitions
            definitions = fresh_definitions(config)
            fixture_terminal = read_json(completed / "fixture-phase-status.json")
            require(set(fixture_terminal["jobs"]) == {"fresh-" + item["fixture_id"] for item in definitions},
                    "Missing assigned fresh fixture job receipt")
            for number in config["selection"]["independent_validation_induced_groups"]:
                key = f"group-{number}"
                results[key] = replay_memory(completed / "memory" / key)
            for definition in definitions:
                key = "fresh-" + definition["fixture_id"]
                job = completed / "fixtures" / key
                results[key] = replay_fixture(definition, job)
                require(read_json(job / "status.json") == fixture_terminal["jobs"][key], "Fresh phase/job receipt differs")
            actual_component_ids = {key for key in request_map if key.startswith(phase_name + "/")}
            require(consumed == actual_component_ids, "Unconsumed or unexpected final memory/fixture calls")
            return {"status": "passed", "jobs": len(results), "exact_component_calls_consumed": len(consumed),
                    "actor_continuation_replay": "Separate required audit; not covered by this component profile"}
        planned = {f"known-group-{number}" for number in (1, 2, 3)}
        definitions = read_json(code / "fixtures/memory-semantic-v3.json")["fixtures"]
        planned |= {definition["fixture_id"] for definition in definitions}
        require(set(terminal["jobs"]) == planned, "Missing assigned component job status")
        capability_id = phase_name + "/structured-capability"
        require(capability_id in request_map and capability_id in response_map, "No completed capability attempt")
        capability_schema = {"type": "object", "properties": {"value": {"const": "ok"}}, "required": ["value"], "additionalProperties": False}
        capability = constrained_payload("qwen-native-dev", config["memory"]["generation"],
                                        [{"role": "system", "content": "Return JSON under the supplied schema."},
                                         {"role": "user", "content": "Set value to ok for this structured-delivery capability probe."}],
                                        capability_schema, 4096)
        text = replay_call(request_map[capability_id], response_map[capability_id], capability)
        require(decode_v4("capability", text, capability_schema) == {"value": "ok"}, "Capability cache differs")
        for number in (1, 2, 3):
            key = f"known-group-{number}"
            job = completed / "memory" / key
            results[key] = replay_memory(job)
            require(read_json(job / "status.json") == terminal["jobs"][key], "Phase/job status mismatch")
        for definition in definitions:
            key = definition["fixture_id"]
            job = completed / "fixtures" / key
            results[key] = replay_fixture(definition, job)
            require(read_json(job / "status.json") == terminal["jobs"][key], "Phase/fixture status mismatch")
        actual_phase_ids = {key for key in request_map if key.startswith(phase_name + "/")}
        require(consumed == actual_phase_ids, "Unconsumed or unexpected phase calls")
        return {"status": "passed", "jobs": len(results), "exact_phase_calls_consumed": len(consumed)}

    guard("replay", jobs_audit)
    result = {"status": "passed_cache_only" if not errors else "failed", "phase": phase_name,
              "inference_commit": phase["code_commit"], "audit_code_path": str(Path(__file__).resolve()),
              "scope": "Memory and controlled components only; actor continuation replay is separate",
              "new_model_calls": 0, "no_HTTP_client_constructed": True,
              "checks": checks, "jobs": results, "errors": errors, "human_gold": False, "main_started": False}
    if calibration:
        result["calibration_audit_profile"] = {
            "contract_sha256": sha256(config_path.read_bytes()).hexdigest(),
            "preflight_sha256": sha256((args.calibration_preflight / "preflight.json").read_bytes()).hexdigest(),
            "audit_script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "review_inputs_sha256": pre["review_inputs_sha256"], "handoff_world": pre["handoff_world"],
            "global_ledger_sha256": {name: sha256((cycle / "ledger" / name).read_bytes()).hexdigest()
                                     for name in ("requests.jsonl", "responses.jsonl")}}
    atomic_json(output / "audit.json", result)
    print(canonical({"status": result["status"], "jobs": len(results), "errors": len(errors), "new_model_calls": 0}).decode())
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
