"""Phase-gated v4 DEV execution. Main collection is not an available command."""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import time
import traceback

import httpx
from transformers import AutoTokenizer

from . import smoke
from .abe_runner import ServiceFailure
from .artifacts import atomic_json, canonical, fingerprint, read_json
from .cycle_budget import AccountingFailure, CycleBudget
from .memory import apply_verdicts, build_packages, require
from .memory_v3 import fixture_source, fixture_eligible, fixture_mapping
from .memory_v4 import constrained_payload, decode_v4, full_delivery_v4, messages_v4, record_packer, resolve_v4


CODE, ROOT, WORK, REVIEW = Path("/code"), Path("/cycle"), Path("/work"), Path("/review")
ALIAS = "qwen-native-dev"


def settings():
    config = read_json(CODE / "configs/readiness-cycle-v4.json")
    base = read_json(CODE / config["base_runtime_config"])
    assets = {stage: {"prompt": (CODE / spec["prompt"]).read_text(), "schema": read_json(CODE / spec["schema"])}
              for stage, spec in config["memory"]["stages"].items()}
    return config, base, assets


def payload(stage, source, candidates, config, assets):
    messages = messages_v4(stage, source, candidates, assets[stage])
    return constrained_payload(ALIAS, config["memory"]["generation"], messages, assets[stage]["schema"],
                               config["memory"]["stages"][stage]["max_output_tokens"])


def content(response):
    response.raise_for_status()
    body = response.json()
    require(len(body["choices"]) == 1 and body["choices"][0]["finish_reason"] == "stop", "Incomplete structured completion")
    message = body["choices"][0]["message"]
    require(not message.get("tool_calls") and not message.get("reasoning") and not message.get("reasoning_content"), "Unexpected native tool/reasoning channel")
    require((body["usage"].get("completion_tokens_details") or {}).get("reasoning_tokens", 0) == 0, "Unexpected thinking tokens")
    require(isinstance(message.get("content"), str) and message["content"].strip(), "Empty structured response")
    return message["content"]


def phase_identity(config, base, assets):
    return {"run_id": config["run_id"], "phase": os.environ.get("CYCLE_PHASE", "development-memory-v1"),
            "code_commit": os.environ["ADAPTER_COMMIT"], "config_sha256": sha256((CODE / "configs/readiness-cycle-v4.json").read_bytes()).hexdigest(),
            "assets_sha256": fingerprint(assets), "limits": config["limits"], "allocations": config["allocations"],
            "model": base["model"], "image_digest": base["runtime"]["image_digest"],
            "actor_sampler": base["generation"], "builder_sampler": config["memory"]["generation"],
            "source_serialization": config["memory"]["source_serialization"], "content_serialization": config["memory"]["actor_serialization"],
            "selection_policy": config["memory"]["selection_policy"], "main_started": False}


def verify_final_DEV_freeze(identity):
    freeze_path = REVIEW / "final-DEV-validation-freeze.json"
    freeze = read_json(freeze_path)
    require(freeze["status"] == "reviewed_bounded_DEV_validation_freeze" and freeze["main_allowed"] is False, "No reviewed final DEV freeze")
    for key in ("code_commit", "config_sha256", "assets_sha256"):
        require(freeze[key] == identity[key], "Final DEV freeze changed after source access: " + key)
    require(freeze.get("design_message_id") and freeze.get("developmental_review_sha256"), "DEV freeze provenance missing")
    return freeze


def verify_dispatch(identity):
    dispatch_path = REVIEW / ("dispatch-" + identity["phase"] + ".json")
    receipt = read_json(dispatch_path)
    require(receipt["status"] == "authorized_bounded_DEV_phase", "Reviewed phase dispatch required")
    require(identity["phase"] in receipt["allowed_phases"], "Phase not included in dispatch")
    for key in ("code_commit", "config_sha256", "assets_sha256"):
        require(receipt[key] == identity[key], "Review/actual identity mismatch: " + key)
    require(receipt.get("design_dispatch_message_id") and receipt.get("human_authority_message_id") == "01a16e81-449a-7152-a8f6-77d8a0a37419", "No authority provenance")
    review = REVIEW / receipt["consistency_review_file"]
    require(sha256(review.read_bytes()).hexdigest() == receipt["consistency_review_sha256"], "Consistency review changed")
    require(read_json(review)["status"] in {"pass", "pass_for_phase"}, "Blocking consistency review")
    require(read_json(Path("/preflight/phase-manifest.json")) == identity, "CPU preflight differs from the live phase")
    require(sha256(Path("/preflight/preflight.json").read_bytes()).hexdigest() == receipt["preflight_sha256"], "CPU preflight receipt changed")
    return {"receipt": receipt, "receipt_sha256": sha256(dispatch_path.read_bytes()).hexdigest()}


def known_sources():
    return {1: Path("/previous/regression-v2/memory/source.json"),
            2: Path("/previous/heldout/memory/group-2/source.json"),
            3: Path("/previous/heldout/memory/group-3/source.json")}


def fresh_definitions(config):
    spec = config["fresh_fixtures"]
    require(sha256((Path("/fresh-fixtures") / spec["lock_file"]).read_bytes()).hexdigest() == spec["lock_sha256"], "Fresh expected lock changed after freeze")
    lock = read_json(Path("/fresh-fixtures") / spec["lock_file"])
    for name in (spec["input_file"], spec["component_domain_file"]):
        require(sha256((Path("/fresh-fixtures") / name).read_bytes()).hexdigest() == lock["file_sha256"][name], "Fresh input/domain lock mismatch")
    definitions = read_json(Path("/fresh-fixtures") / spec["input_file"])["fixtures"]
    domain = {item["fixture_id"]: item for item in read_json(Path("/fresh-fixtures") / spec["component_domain_file"])["fixtures"]}
    result = []
    for item in definitions:
        value = deepcopy(item)
        value["P_eligible"] = domain[item["fixture_id"]]["P_eligible"]
        result.append(value)
    require(len(result) == 16 and sum(len(item["candidates"]) for item in result) == 37, "Wrong locked fresh matrix")
    return result


def smoke_payload(config):
    schema = {"type": "object", "properties": {"value": {"const": "ok"}}, "required": ["value"], "additionalProperties": False}
    return constrained_payload(ALIAS, config["memory"]["generation"],
                               [{"role": "system", "content": "Return JSON under the supplied schema."},
                                {"role": "user", "content": "Set value to ok for this structured-delivery capability probe."}], schema, 4096)


def preflight():
    config, base, assets = settings()
    identity = phase_identity(config, base, assets)
    if identity["phase"] == "development-prefix-v1":
        from .abe_cycle import actor_preflight
        actor_preflight(config, base, assets, WORK, [4, 5])
        atomic_json(WORK / "phase-manifest.json", identity)
        atomic_json(WORK / "preflight.json", {"status": "passed_no_model_calls", "identity": identity,
                    "actor_preflight": read_json(WORK / "actor-preflight.json"), "new_model_calls": 0})
        return
    if identity["phase"] == "validation-prefix-v1":
        verify_final_DEV_freeze(identity)
        from .abe_cycle import actor_preflight
        actor_preflight(config, base, assets, WORK, [6, 7])
        atomic_json(WORK / "phase-manifest.json", identity)
        atomic_json(WORK / "preflight.json", {"status": "passed_no_model_calls", "identity": identity,
                    "actor_preflight": read_json(WORK / "actor-preflight.json"), "new_model_calls": 0})
        return
    if identity["phase"] == "validation-continuations-v1":
        freeze = verify_final_DEV_freeze(identity)
        probes, sources = {}, {}
        prefix_identity = read_json(ROOT / "validation-prefix-v1/phase-manifest.json")
        for key in ("code_commit", "config_sha256", "assets_sha256"):
            require(prefix_identity[key] == identity[key], "Fresh code or assets retuned after prefix")
        for number in (6, 7):
            source_path = ROOT / f"validation-prefix-v1/prefix/group-{number}/source.json"
            source = read_json(source_path)
            sources[str(number)] = {"archive_sha256": source["archive_sha256"], "file_sha256": sha256(source_path.read_bytes()).hexdigest()}
            for stage in ("S", "C"):
                probes[f"group-{number}/{stage}"] = payload(stage, source, [], config, assets)
        for definition in fresh_definitions(config):
            source, candidates = fixture_source(definition)
            for stage, selected in (("V", candidates), ("P", fixture_eligible(definition, candidates))):
                probes[f"fresh-{definition['fixture_id']}/{stage}"] = payload(stage, source, selected, config, assets)
        tokenizer = AutoTokenizer.from_pretrained("/model", local_files_only=True)
        for key, body in probes.items():
            token_ids = tokenizer.apply_chat_template(body["messages"], tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False)
            require(len(token_ids) + body["max_tokens"] <= 32768, "Fresh full input does not fit: " + key)
        atomic_json(WORK / "phase-manifest.json", identity)
        atomic_json(WORK / "assets.json", assets)
        atomic_json(WORK / "wire-probes.json", probes)
        atomic_json(WORK / "preflight.json", {"status": "passed_no_model_calls", "identity": identity, "sources": sources,
                    "maximum_memory_calls": 8, "maximum_fresh_fixture_calls": 32, "maximum_continuation_calls": 240,
                    "new_model_calls": 0, "main_started": False})
        return
    if identity["phase"] == "development-checkpoint-memory-v1":
        probes = {}
        sources = {}
        for number in (4, 5):
            path = ROOT / f"development-prefix-v1/prefix/group-{number}/source.json"
            source = read_json(path)
            require(read_json(path.parent / "status.json")["status"] == "prefix_collected", "Prefix incomplete; cannot build its memory")
            sources[str(number)] = {"archive_sha256": source["archive_sha256"], "file_sha256": sha256(path.read_bytes()).hexdigest()}
            for stage in ("S", "C"):
                body = payload(stage, source, [], config, assets)
                token_ids = AutoTokenizer.from_pretrained("/model", local_files_only=True).apply_chat_template(body["messages"], tokenize=True,
                             add_generation_prompt=True, enable_thinking=False, return_dict=False)
                require(len(token_ids) + body["max_tokens"] <= 32768, "Actual A/B source full input overflow")
                probes[f"checkpoint-group-{number}/{stage}"] = body
        atomic_json(WORK / "phase-manifest.json", identity)
        atomic_json(WORK / "assets.json", assets)
        atomic_json(WORK / "wire-probes.json", probes)
        atomic_json(WORK / "preflight.json", {"status": "passed_no_model_calls", "identity": identity, "sources": sources,
                    "maximum_calls_this_component_phase": 8, "new_model_calls": 0, "main_started": False})
        return
    tokens = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    definitions = read_json(CODE / "fixtures/memory-semantic-v3.json")["fixtures"]
    probes, counts = {}, {}
    for number, path in known_sources().items():
        source = read_json(path)
        for stage in ("S", "C"):
            key = f"known-group-{number}/{stage}"
            probes[key] = payload(stage, source, [], config, assets)
    for definition in definitions:
        source, candidates = fixture_source(definition)
        for stage, selected in (("V", candidates), ("P", fixture_eligible(definition, candidates))):
            probes[definition["fixture_id"] + "/" + stage] = payload(stage, source, selected, config, assets)
    probes["structured-capability"] = smoke_payload(config)
    for key, body in probes.items():
        ids = tokens.apply_chat_template(body["messages"], tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False)
        require(len(ids) + body["max_tokens"] <= 32768, "Preflight full context overflow: " + key)
        counts[key] = {"rendered_input_tokens": len(ids), "output_reserve": body["max_tokens"], "request_sha256": fingerprint(body)}
    atomic_json(WORK / "phase-manifest.json", identity)
    atomic_json(WORK / "assets.json", assets)
    atomic_json(WORK / "wire-probes.json", probes)
    atomic_json(WORK / "preflight.json", {"status": "passed_no_model_calls", "identity": identity,
                "known_memory_groups": [1, 2, 3], "known_fixture_count": 8, "maximum_calls_this_component_phase": 29,
                "budget_residual": "8 reserved for memory of actual DEV4/5 A/B checkpoints; 3 only separately reviewed stage-specific v2 fixes",
                "probes": counts, "new_model_calls": 0, "fresh6_7_sources_opened": False, "main_started": False})


def dispatch(budget, body, logical_id, allocation, job):
    phase = os.environ["CYCLE_PHASE"]
    key = logical_id.removeprefix(phase + "/")
    probes = read_json(Path("/preflight/wire-probes.json"))
    if key in probes:
        require(body == probes[key], "Actual fixed wire differs from the reviewed probe: " + key)
    try:
        return budget.dispatch(body, logical_id, allocation, job=job)
    except httpx.TimeoutException as error:
        raise ServiceFailure(str(error), kind="timeout") from error
    except httpx.HTTPError as error:
        raise ServiceFailure(str(error)) from error
    except AccountingFailure as error:
        raise ServiceFailure(str(error), kind="accounting_failure") from error


def memory_job(source, job, identity, config, assets, budget, count_tokens, *, allocation="developmental_memory_and_known_fixtures"):
    require(fingerprint(source["archive"]) == source["archive_sha256"], "Permitted archive changed")
    require(not job.exists(), "Immutable memory job already exists")
    job.mkdir(parents=True, mode=0o700)
    atomic_json(job / "source.json", source)
    atomic_json(job / "assets.json", assets)
    atomic_json(job / "manifest.json", {"job_id": identity, "source_archive_sha256": source["archive_sha256"], "assets_sha256": fingerprint(assets)})
    persist = lambda name, value: atomic_json(job / "automatic-withheld" / name, value)
    def generate(stage, candidates_messages):
        body = constrained_payload(ALIAS, config["memory"]["generation"], candidates_messages, assets[stage]["schema"],
                                   config["memory"]["stages"][stage]["max_output_tokens"])
        response = dispatch(budget, body, identity + "/" + stage, allocation, job)
        return content(response)
    try:
        summary = build_packages(source, {"packing": {"archive_wrapper": config["memory"]["archive_wrapper"]}}, assets,
                                 generate, count_tokens, persist, decode=decode_v4, resolve=resolve_v4,
                                 full_archive_content=full_delivery_v4, message_builder=messages_v4, record_packer=record_packer(source["archive"]))
        status = {"status": "built", "summary": summary}
    except Exception as error:
        status = {"status": "failed", "failure_type": type(error).__name__, "traceback": traceback.format_exc()}
        atomic_json(job / "status.json", status)
        if isinstance(error, (ServiceFailure, httpx.HTTPError)):
            raise
    atomic_json(job / "status.json", status)
    return status


def fixture_job(definition, job, identity, config, assets, budget, *, allocation="developmental_memory_and_known_fixtures"):
    require(not job.exists(), "Immutable fixture job already exists")
    job.mkdir(parents=True, mode=0o700)
    source, candidates = fixture_source(definition)
    eligible = fixture_eligible(definition, candidates)
    atomic_json(job / "source.json", source)
    atomic_json(job / "candidates.json", candidates)
    atomic_json(job / "audit-mapping.json", fixture_mapping(definition, source, candidates))
    failures, supported, allowed = {}, None, None
    for stage, selected in (("V", candidates), ("P", eligible)):
        try:
            body = payload(stage, source, selected, config, assets)
            raw = content(dispatch(budget, body, identity + "/" + stage, allocation, job))
            value = decode_v4(stage, raw, assets[stage]["schema"])
            atomic_json(job / "automatic-withheld/stages" / (stage + ".json"), value)
            chosen, _ = apply_verdicts(selected, value["verdicts"], field="verdict" if stage == "V" else "label", retain="supported" if stage == "V" else "allow")
            if stage == "V":
                supported = {item["claim_id"] for item in chosen}
            else:
                allowed = {item["claim_id"] for item in chosen}
        except Exception as error:
            failures[stage] = {"type": type(error).__name__, "traceback": traceback.format_exc()}
            if isinstance(error, (ServiceFailure, httpx.HTTPError)):
                atomic_json(job / "status.json", {"status": "stopped_model_service_failure", "component_failures": failures})
                raise
    eligible_ids = {item["claim_id"] for item in eligible}
    status = {"status": "complete" if not failures else "partial_component_failure", "component_failures": failures,
              "component_p_eligible_ids": sorted(eligible_ids), "actual_v_supported_ids": None if supported is None else sorted(supported),
              "component_p_allowed_ids": None if allowed is None else sorted(allowed),
              "e2e_retained_reference_eligible_ids": None if supported is None or allowed is None else sorted(supported & allowed),
              "off_support_v_accepts_without_p_test": None if supported is None else sorted(supported - eligible_ids)}
    atomic_json(job / "status.json", status)
    return status


def run_client():
    config, base, assets = settings()
    identity = phase_identity(config, base, assets)
    receipt = verify_dispatch(identity)
    require(identity["phase"] in {"development-memory-v1", "development-prefix-v1", "development-checkpoint-memory-v1", "validation-prefix-v1", "validation-continuations-v1"}, "This phase is not yet implemented/reviewed")
    master = {"run_id": config["run_id"], "authority": config["authority"], "limits": config["limits"],
              "allocations": config["allocations"], "selection": config["selection"], "model": base["model"],
              "runtime": base["runtime"], "paid_api_budget_usd": 0}
    master_path = ROOT / "master-manifest.json"
    if master_path.exists():
        require(read_json(master_path) == master, "Do not reset or expand the bounded block")
    else:
        atomic_json(master_path, master)
    atomic_json(WORK / "phase-manifest.json", {**identity, "dispatch": receipt})
    atomic_json(WORK / "assets.json", assets)
    tokens = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    jobs = {}
    with httpx.Client(trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as http:
        budget = CycleBudget(ROOT, config, http, phase_work=WORK)
        if identity["phase"] == "development-prefix-v1":
            from .abe_cycle import prefix_phase
            return prefix_phase(config, base, budget, WORK)
        if identity["phase"] == "validation-prefix-v1":
            from .abe_cycle import validation_prefix_phase
            return validation_prefix_phase(config, base, budget, WORK)
        if identity["phase"] == "validation-continuations-v1":
            verify_final_DEV_freeze(identity)
            reviewed_sources = read_json(Path("/preflight/preflight.json"))["sources"]
            reference_locks = read_json(REVIEW / "source-first-reference-locks.json")
            require(reference_locks["origin"] == "AI_source_only_not_human_gold", "Reference origin must remain explicit")
            for number in (6, 7):
                source_path = ROOT / f"validation-prefix-v1/prefix/group-{number}/source.json"
                require(sha256(source_path.read_bytes()).hexdigest() == reviewed_sources[str(number)]["file_sha256"], "Reviewed fresh source changed")
                require(reference_locks["groups"][str(number)]["source_file_sha256"] == reviewed_sources[str(number)]["file_sha256"] and
                        reference_locks["groups"][str(number)].get("reference_sha256"), "Source-first lock missing before outputs")
            from .abe_cycle import validation_continuations_phase
            validation_continuations_phase(config, base, assets, budget, WORK)
            for definition in fresh_definitions(config):
                key = "fresh-" + definition["fixture_id"]
                jobs[key] = fixture_job(definition, WORK / "fixtures" / key, identity["phase"] + "/" + key, config, assets, budget,
                                        allocation="fresh_controlled_fixtures")
            atomic_json(WORK / "fixture-phase-status.json", {"status": "finished_with_explicit_job_statuses", "jobs": jobs,
                        "global_budget": budget.state(), "main_started": False})
            return
        if identity["phase"] == "development-checkpoint-memory-v1":
            reviewed = read_json(Path("/preflight/preflight.json"))["sources"]
            for number in (4, 5):
                source_path = ROOT / f"development-prefix-v1/prefix/group-{number}/source.json"
                source = read_json(source_path)
                require(sha256(source_path.read_bytes()).hexdigest() == reviewed[str(number)]["file_sha256"], "Reviewed A/B archive changed")
                key = f"checkpoint-group-{number}"
                jobs[key] = memory_job(source, WORK / "memory" / key, identity["phase"] + "/" + key, config, assets, budget,
                                      lambda text: len(tokens.encode(text, add_special_tokens=False)))
            atomic_json(WORK / "phase-status.json", {"status": "finished_with_explicit_job_statuses", "jobs": jobs,
                        "global_budget": budget.state(), "main_started": False})
            return
        probe = dispatch(budget, smoke_payload(config), identity["phase"] + "/structured-capability", "developmental_memory_and_known_fixtures", WORK / "capability")
        require(decode_v4("capability", content(probe), smoke_payload(config)["response_format"]["json_schema"]["schema"]) == {"value": "ok"}, "Structured capability probe failed")
        for number, path in known_sources().items():
            source = read_json(path)
            key = f"known-group-{number}"
            jobs[key] = memory_job(source, WORK / "memory" / key, identity["phase"] + "/" + key, config, assets, budget,
                                    lambda text: len(tokens.encode(text, add_special_tokens=False)))
        definitions = read_json(CODE / "fixtures/memory-semantic-v3.json")["fixtures"]
        for definition in definitions:
            key = definition["fixture_id"]
            jobs[key] = fixture_job(definition, WORK / "fixtures" / key, identity["phase"] + "/" + key, config, assets, budget)
        atomic_json(WORK / "phase-status.json", {"status": "finished_with_explicit_job_statuses", "jobs": jobs,
                    "global_budget": budget.state(), "main_started": False})


def main():
    os.umask(0o077)
    require(os.getuid() != 0, "Non-root Docker required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        return preflight()
    if args.client:
        return run_client()
    config, base, assets = settings()
    identity = phase_identity(config, base, assets)
    verify_dispatch(identity)
    require(identity["phase"] in {"development-memory-v1", "development-prefix-v1", "development-checkpoint-memory-v1", "validation-prefix-v1", "validation-continuations-v1"}, "No unreviewed phase startup")
    if identity["phase"].startswith("validation-"):
        verify_final_DEV_freeze(identity)
    deadline = min(float(os.environ["PACKAGE_DEADLINE_UNIX"]), time.time() + config["limits"]["gpu_service_wall_seconds"])
    smoke.supervise(base, deadline, client_module="collusion_handoff.readiness_cycle", alias=ALIAS,
                    scope="v4_bounded_developmental_memory_only", server_extra_args=("--structured-outputs-config.backend", "xgrammar"))


if __name__ == "__main__":
    main()
