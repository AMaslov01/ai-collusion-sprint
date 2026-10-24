"""Finite v3 development, held-out native checkpoints and component fixtures."""

import argparse
import fcntl
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import time
import traceback

import httpx

from . import smoke
from .artifacts import append_event, atomic_json, fingerprint, read_json
from .checkpoint import actor_state, load_checkpoint, restore_actor, save_checkpoint, world_state
from .cycle_budget import CycleBudget, NativeCycleTransport, utc
from .memory import apply_verdicts, build_packages, require, stage_messages
from .memory_format import decode_format
from .memory_runner import source_first, tokenizer
from .memory_source import build_source
from .memory_v3 import decoder_v3, derive_verification, fixture_source, full_delivery, resolve_v3
from .model_gateway import WireJournal, install_native_actor_gateway
from .native_adapter import NativeBudget, MeteredClient, ObservedExecutor, install_data, make_agent, policy_for
from .native_data import base_actor_view, task_actor_view
from .native_replay import CachedTools


ROOT, WORK, CODE = Path("/cycle"), Path("/work"), Path("/code")
UPSTREAM = Path("/opt/ai-collusion/sources/MemoryArena")
CONFIG = CODE / "configs/repair-cycle-v3.json"
BASE_CONFIG = Path("/basecode/configs/dev-package.draft.json")
ALIAS = "qwen-native-dev"


def settings():
    config = read_json(CONFIG)
    base = read_json(BASE_CONFIG)
    contract = read_json(Path("/basecode/configs/memory-group1.json"))
    contract["stages"] = config["stages"]
    assets = {stage: {"prompt": (CODE / item["prompt"]).read_text(), "schema": read_json(CODE / item["schema"])} for stage, item in config["stages"].items()}
    definitions = read_json(CODE / "fixtures/memory-semantic-v3.json")
    return config, base, contract, assets, definitions


def master_manifest(config, base):
    value = {"run_id": config["run_id"], "scope": config["scope"], "authority": config["authority"],
             "limits": config["limits"], "allocations": config["allocations"], "selection": config["selection"],
             "model": base["model"], "image_digest": base["runtime"]["image_digest"],
             "upstream_commit": base["code"]["upstream_commit"], "actor_generation": base["generation"],
             "data_sha256": base["data"]["file_hashes"]["sha256"],
             "catalog_identity_sha256": base["data"]["tool_input_manifest"]["sha256"]}
    path = ROOT / "master-manifest.json"
    if path.exists():
        require(read_json(path) == value, "New cycle identity/limits changed; never reset budget")
    else:
        atomic_json(path, value)
    return value


def phase_manifest(config, assets, definitions):
    review = config["consistency_review"]
    for filename, expected in ((review["review_file"], review["review_sha256"]), (review["expected_fixture_lock_file"], review["expected_fixture_lock_sha256"])):
        require(sha256((Path("/review") / filename).read_bytes()).hexdigest() == expected, "Consistency review or expected fixture lock changed")
    value = {"phase": os.environ["CYCLE_PHASE"], "code_commit": os.environ["ADAPTER_COMMIT"],
             "contract_sha256": sha256(CONFIG.read_bytes()).hexdigest(), "assets_sha256": fingerprint(assets),
             "fixture_definitions_sha256": fingerprint(definitions), "fixture_file_sha256": sha256((CODE / "fixtures/memory-semantic-v3.json").read_bytes()).hexdigest(),
             "consistency_review": config["consistency_review"], "cutoff": config["limits"]["absolute_cutoff_utc"]}
    path = WORK / "phase-manifest.json"
    if path.exists():
        require(read_json(path) == value, "Phase assets/code changed")
    else:
        atomic_json(path, value)
    atomic_json(WORK / "assets.json", assets)
    return value


def content_of(response):
    raw = response.json()
    require(len(raw["choices"]) == 1 and raw["choices"][0]["finish_reason"] == "stop", "Incomplete builder output")
    message = raw["choices"][0]["message"]
    require(not message.get("tool_calls") and not message.get("reasoning") and not message.get("reasoning_content"), "Unexpected tools/thinking from builder")
    require((raw["usage"].get("completion_tokens_details") or {}).get("reasoning_tokens", 0) == 0, "Thinking tokens are nonzero")
    require(isinstance(message.get("content"), str) and bool(message["content"].strip()), "Empty builder response")
    return message["content"]


def payload_for(stage, messages, contract):
    return {"model": ALIAS, "messages": messages, **contract["model"]["generation"],
            "max_tokens": contract["stages"][stage]["max_output_tokens"], "stream": False}


def memory_job(source, job, label, allocation, contract, assets, budget, tokens):
    job.mkdir(parents=True, mode=0o700)
    require(not (job / "status.json").exists(), "Existing memory job is immutable; audit instead of rerun")
    atomic_json(job / "source.json", source)
    atomic_json(job / "assets.json", assets)
    atomic_json(job / "manifest.json", {"job_id": label, "source_archive_sha256": source["archive_sha256"],
                "assets_sha256": fingerprint(assets), "code_commit": os.environ["ADAPTER_COMMIT"], "allocation": allocation})
    persist = lambda name, value: atomic_json(job / "automatic-withheld" / name, value)

    def generate(stage, messages):
        return content_of(budget.dispatch(payload_for(stage, messages, contract), label + "/" + stage, allocation, job=job))

    try:
        summary = build_packages(source, contract, assets, generate, lambda text: len(tokens.encode(text, add_special_tokens=False)),
                                 persist, decode=decoder_v3(persist), resolve=resolve_v3, full_archive_content=full_delivery)
        atomic_json(job / "status.json", {"status": "built", "summary": summary, "human_gold": False})
    except Exception:
        atomic_json(job / "status.json", {"status": "failed", "traceback": traceback.format_exc(), "hidden_semantic_repair": False})
    append_event(ROOT / "progress.jsonl", {"event": "memory_job_finished", "job_id": label, "status": read_json(job / "status.json")["status"], "timestamp": utc()})


class BoundedNativeBudget(NativeBudget):
    def __init__(self, *args, cycle_budget, allocation, **kwargs):
        super().__init__(*args, **kwargs)
        self.cycle_budget, self.allocation = cycle_budget, allocation

    def before_call(self, payload):
        smoke.admission(self.job_deadline)
        self.cycle_budget.check(self.context()["logical_call_id"], self.allocation)
        limits = self.config["limits"]
        require(self.calls < 30, "Traveler call limit reached")
        count = self.cycle_budget.preview(payload, deadline=self.job_deadline)
        require(count <= 28672, "Native fully rendered input exceeds cap")
        require(self.tokens + count + payload["max_tokens"] <= 200000, "Traveler token ceiling reached")
        self.calls += 1
        self.input_counts.append(count)
        self.last_context = {"logical_call_id": f"{self.job_id}/A/turn-{self.calls}", "role": "A", "attempt": 1}
        append_event(self.events, {"event": "model_attempt_admitted", "timestamp": utc(), **self.last_context,
                                  "input_tokens": count, "output_reserve": payload["max_tokens"]})


def native_group(group_id, config, budget):
    install_data(config)
    from env.env_systems.travel_env import TravelPlannerEnvironment, _parse_person_plan_from_result, SLOTS
    from run_travel import format_person_plan
    job = WORK / f"native/group-{group_id}"
    job.mkdir(parents=True, mode=0o700)
    require(not (job / "manifest.json").exists(), "Native group already attempted; never regenerate")
    environment = TravelPlannerEnvironment({"judgement_mode": "none"})
    observation = environment.reset(seed=group_id)
    require(observation["group_id"] == group_id, "Actual group identity changed")
    agent = make_agent(config)
    client = agent.client
    base = base_actor_view(observation)
    agent.set_base_person(base["name"], base["query"], format_person_plan(base["name"], base["daily_plans"]))
    science = {"scope": "repair_v3_native", "group_id": group_id, "new_travelers": 2,
               "upstream_commit": config["code"]["upstream_commit"], "catalog_identity_sha256": config["data"]["tool_input_manifest"]["sha256"],
               "data_sha256": config["data"]["file_hashes"]["sha256"], "generation": config["generation"],
               "code_commit": os.environ["ADAPTER_COMMIT"], "model": config["model"], "runtime": read_json(WORK / "resolved-engine.json")}
    identity = fingerprint(science)
    atomic_json(job / "manifest.json", {"scientific": science, "scientific_fingerprint": identity})
    events = job / f"events/group-{group_id}.jsonl"
    (job / "io").mkdir(mode=0o700)
    save_checkpoint(job / f"checkpoints/group-{group_id}/traveler-0", agent=agent, environment=environment,
                    group=environment.current_group, next_round=1, scientific_fingerprint=identity)
    native_budget = BoundedNativeBudget(config, budget.http, budget.deadline(), events, job,
                                       cycle_budget=budget, allocation=f"native_group{group_id}")
    install_native_actor_gateway(client, policy=policy_for(config), journal=WireJournal(job / "io"),
        call_context=native_budget.context, before_call=native_budget.before_call, timeout_seconds=900,
        inner=NativeCycleTransport(budget, native_budget, f"native_group{group_id}"))
    agent.client = MeteredClient(client, native_budget)
    agent.executor = ObservedExecutor(agent.executor, native_budget, config["data"]["tool_input_manifest"]["sha256"])
    try:
        for number in (1, 2):
            task = task_actor_view(observation, number)
            native_budget.start_job(f"native/group-{group_id}/traveler-{number}")
            append_event(events, {"event": "ISSUE_TASK", "timestamp": utc(), "actor_task": task})
            agent.prepare_for_person(name=task["name"], round_idx=number, include_previous_plans=True, memory_context=None, memory_system=None)
            action = agent.act(task["query"])
            answer = next(item for item in observation["answers"] if item["round_idx"] == number)
            _, reward, info = environment.step(action, ground_truth={"name": task["name"], "daily_plans": answer["daily_plans"], "judgement_mode": "none"}, need_judge=True)
            require(not info.get("judgement") and not agent.previous_judgement, "Judgement leaked into actor history")
            parsed = _parse_person_plan_from_result(action, task["name"])
            valid = bool(parsed) and all(all(slot in day for slot in SLOTS) for day in parsed)
            checkpoint = save_checkpoint(job / f"checkpoints/group-{group_id}/traveler-{number}", agent=agent,
                environment=environment, group=environment.current_group, next_round=number+1, scientific_fingerprint=identity)
            result = {"job_id": native_budget.job_id, "actor_task": task, "final_text": action, "parsed_plan": parsed,
                      "parse_valid": valid, "model_calls": native_budget.calls, "total_input_output_tokens": native_budget.tokens,
                      "completion": bool(action) and native_budget.finishes[-1] == "stop",
                      "protocol_valid": all(reason in {"stop", "tool_calls"} for reason in native_budget.finishes),
                      "native_approximate_secondary_reward": reward, "scientific_fingerprint": identity,
                      "checkpoint_manifest": checkpoint, "native_result": asdict(agent.last_result)}
            atomic_json(job / f"results/group-{group_id}-traveler-{number}.json", result)
            require(result["parse_valid"] and result["completion"] and result["protocol_valid"], "Native output incomplete or invalid; do not replace group")
        atomic_json(job / "status.json", {"status": "complete", "new_travelers": 2, "third_fourth_travelers_generated": False})
    except Exception:
        atomic_json(job / "status.json", {"status": "failed", "traceback": traceback.format_exc()})
        raise
    finally:
        client.client.close()
        environment.close()
    return job


def replay_native(job, group_id, config, *, output=None):
    install_data(config)
    from env.env_systems.travel_env import TravelPlannerEnvironment
    requests, responses = smoke.journal_rows(job / "io/requests.jsonl"), smoke.journal_rows(job / "io/responses.jsonl")
    require(len(requests) == len(responses) and requests, "Incomplete native IO")
    environment = TravelPlannerEnvironment({"judgement_mode": "none"})
    observation = environment.reset(seed=group_id)
    agent = make_agent(config)
    native_client = agent.client
    science = read_json(job / "manifest.json")["scientific_fingerprint"]
    _, initial = load_checkpoint(job / f"checkpoints/group-{group_id}/traveler-0", science)
    restore_actor(agent, initial["actor"])
    tools = CachedTools(smoke.journal_rows(job / f"events/group-{group_id}.jsonl"))
    agent.executor = tools
    cursor = 0
    destination = job if output is None else Path(output)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    replay_io = destination / "replay-io"
    replay_io.mkdir(exist_ok=True, mode=0o700)

    def context():
        require(cursor < len(requests), "Replay requested extra inference")
        return {"logical_call_id": requests[cursor]["logical_call_id"], "role": "A", "attempt": 1, "cache_only": True}

    def before(payload):
        require(fingerprint(payload) == requests[cursor]["request_sha256"], "Native replay input changed")

    def cached(request):
        nonlocal cursor
        response = responses[cursor]
        require(response.get("status_code") == 200, "Native cached response failed")
        cursor += 1
        return httpx.Response(200, content=response["body_utf8"].encode(), headers={"Content-Type": "application/json"})

    install_native_actor_gateway(native_client, policy=policy_for(config), journal=WireJournal(replay_io),
        call_context=context, before_call=before, timeout_seconds=900, inner=httpx.MockTransport(cached))
    try:
        for number in (1, 2):
            task = task_actor_view(observation, number)
            agent.prepare_for_person(name=task["name"], round_idx=number, include_previous_plans=True, memory_context=None, memory_system=None)
            action = agent.act(task["query"])
            result = read_json(job / f"results/group-{group_id}-traveler-{number}.json")
            require(action == result["final_text"], "Replayed final text changed")
            answer = next(item for item in observation["answers"] if item["round_idx"] == number)
            environment.step(action, ground_truth={"name": task["name"], "daily_plans": answer["daily_plans"], "judgement_mode": "none"}, need_judge=True)
            _, snapshot = load_checkpoint(job / f"checkpoints/group-{group_id}/traveler-{number}", science)
            require(actor_state(agent) == snapshot["actor"] and world_state(environment) == snapshot["world"], "Replayed actor/world state changed")
        require(cursor == len(requests) and not tools.remaining, "Replay did not consume exact IO")
        atomic_json(destination / "audit.json", {"status": "passed_cache_only", "new_model_calls": 0, "cached_calls": cursor})
    finally:
        native_client.client.close()
        environment.close()


def fixture_job(definition, contract, assets, budget):
    identity = definition["fixture_id"]
    job = WORK / "fixtures" / identity
    job.mkdir(parents=True, mode=0o700)
    source, candidates = fixture_source(definition)
    atomic_json(job / "source.json", source)
    atomic_json(job / "candidates.json", candidates)
    atomic_json(job / "assets.json", assets)
    persist = lambda name, value: atomic_json(job / "automatic-withheld" / name, value)
    decode = decoder_v3(persist)

    def call(stage, values):
        messages = stage_messages(stage, source["archive"], source["archive_sha256"], values, assets[stage]["schema"], assets[stage]["prompt"],
                                  event_fields=contract["stage_input_envelope"]["model_event_fields"])
        raw = content_of(budget.dispatch(payload_for(stage, messages, contract), identity + "/" + stage, "semantic_fixtures", job=job))
        result = decode(stage, raw, assets[stage]["schema"])
        persist(f"stages/{stage}.json", result)
        return result
    try:
        verification = call("V", candidates)
        supported, errors = apply_verdicts(candidates, verification["verdicts"], field="verdict", retain="supported")
        eligible_ids = {identity + "/" + alias for alias in definition["P_eligible"]}
        eligible = [claim for claim in candidates if claim["claim_id"] in eligible_ids]
        policy = call("P", eligible)
        allowed, p_errors = apply_verdicts(eligible, policy["verdicts"], field="label", retain="allow")
        supported_ids = {item["claim_id"] for item in supported}
        atomic_json(job / "status.json", {"status": "complete", "component_p_eligible_ids": sorted(eligible_ids),
                    "actual_v_supported_ids": sorted(supported_ids), "component_p_allowed_ids": [item["claim_id"] for item in allowed],
                    "e2e_retained_reference_eligible_ids": [item["claim_id"] for item in allowed if item["claim_id"] in supported_ids],
                    "off_support_v_accepts_without_p_test": sorted(supported_ids - eligible_ids), "V_errors": errors, "P_errors": p_errors,
                    "natural_end_to_end_population_effect": "not_estimated_controlled_component_fixture"})
    except Exception:
        atomic_json(job / "status.json", {"status": "failed", "traceback": traceback.format_exc()})


def run_client():
    config, base, contract, assets, definitions = settings()
    require(config["status"] == "reviewed" and config["consistency_review"]["status"] == "pass", "Pre-call consistency review required")
    master_manifest(config, base)
    phase_manifest(config, assets, definitions)
    phase = os.environ["CYCLE_PHASE"]
    tokens = tokenizer()
    with httpx.Client(trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as http:
        budget = CycleBudget(ROOT, config, http, phase_work=WORK)
        if phase in {"regression-v1", "regression-v2"}:
            source = read_json(Path("/regression-source/source.json"))
            require(source["archive_sha256"] == "d99a521528ec85f2010b30a7a9e165df97ec55d2c5c4dc7b2987279180df8017", "Wrong development source")
            memory_job(source, WORK / "memory", phase, "regression", contract, assets, budget, tokens)
        elif phase == "heldout":
            atomic_json(WORK / "heldout-freeze.json", {"phase": read_json(WORK / "phase-manifest.json"), "frozen_before_native_calls": True})
            for group_id in (2, 3):
                try:
                    job = native_group(group_id, base, budget)
                    replay_native(job, group_id, base)
                    source = build_source(job, UPSTREAM, group_id=group_id)
                    destination = ROOT / f"sources/group-{group_id}"
                    atomic_json(destination / "source.json", source)
                    source_first(source, work=destination)
                    append_event(ROOT / "progress.jsonl", {"event": "source_ready", "group_id": group_id, "archive_sha256": source["archive_sha256"], "timestamp": utc()})
                    memory_job(source, WORK / f"memory/group-{group_id}", f"memory/group-{group_id}", f"memory_group{group_id}", contract, assets, budget, tokens)
                except Exception:
                    atomic_json(WORK / f"group-{group_id}-failure.json", {"traceback": traceback.format_exc(), "replacement_group": False})
            for definition in definitions["fixtures"]:
                fixture_job(definition, contract, assets, budget)
        else:
            raise RuntimeError("Unapproved cycle phase")
        atomic_json(WORK / "phase-status.json", {"status": "finished_with_explicit_job_statuses", "phase": phase,
                    "global_budget": budget.state(), "main_started": False})


def checks():
    config, base, contract, assets, definitions = settings()
    master_manifest(config, base)
    phase_manifest(config, assets, definitions)
    source = read_json(Path("/regression-source/source.json"))
    require(build_source(Path("/native-old"), UPSTREAM) == source, "Source refactor changed original group1")
    tokens = tokenizer()
    for definition in definitions["fixtures"]:
        _, claims = fixture_source(definition)
        require(len(claims) == len(definition["candidates"]), "Fixture candidates deduplicated unexpectedly")
        require({definition["fixture_id"] + "/" + alias for alias in definition["P_eligible"]} <= {item["claim_id"] for item in claims}, "Unknown fixture P ID")
    counts = {}
    for stage in ("S", "C"):
        messages = stage_messages(stage, source["archive"], source["archive_sha256"], [], assets[stage]["schema"], assets[stage]["prompt"], event_fields=contract["stage_input_envelope"]["model_event_fields"])
        ids = tokens.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False)
        require(isinstance(ids, list) and all(type(item) is int for item in ids), "Tokenizer returned unexpected type")
        counts[stage] = len(ids)
        require(len(ids) + contract["stages"][stage]["max_output_tokens"] <= 32768, "Regression preflight overflow")
    atomic_json(WORK / "preflight.json", {"status": "passed_no_model_calls", "input_counts": counts, "fixtures": len(definitions["fixtures"]), "assets_sha256": fingerprint(assets)})


def main():
    os.umask(0o077)
    require(os.getuid() != 0, "Non-root Docker required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--checks", action="store_true")
    args = parser.parse_args()
    config, base, contract, assets, definitions = settings()
    if args.checks:
        checks()
    elif args.client:
        with (ROOT / "worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run_client()
    else:
        with httpx.Client(trust_env=False) as http:
            budget = CycleBudget(ROOT, config, http, phase_work=WORK)
            smoke.supervise(base, budget.deadline(), client_module="collusion_handoff.repair_cycle", alias=ALIAS, scope="bounded_repair_cycle_v3")


if __name__ == "__main__":
    main()
