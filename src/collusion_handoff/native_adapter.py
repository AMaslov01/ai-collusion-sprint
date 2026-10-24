"""Bounded native-history collection; the first group stops before memory builds."""

import argparse
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
import traceback

import httpx

from .artifacts import append_event, atomic_json, fingerprint, read_json
from .checkpoint import actor_state, save_checkpoint
from .model_gateway import ActorRequestPolicy, WireJournal, install_native_actor_gateway
from .native_data import base_actor_view, task_actor_view, install_pinned_loader
from .smoke import admission, URL, WORK, CONFIG


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def install_data(config):
    return install_pinned_loader(Path("/tasks/data.jsonl"), config["data"]["file_hashes"]["sha256"])


def make_agent(config):
    from agent.travel_planner import TravelPlannerAgent
    os.environ["OPENAI_API_KEY"] = "local-inference"
    os.environ["OPENAI_API_BASE"] = URL + "/v1"
    return TravelPlannerAgent(model_name=config["model"]["served_alias"],
                              temperature=config["generation"]["temperature"],
                              max_steps=config["limits"]["max_steps"], db_path="/database")


def policy_for(config):
    return ActorRequestPolicy(generation=config["generation"], served_alias=config["model"]["served_alias"],
                              max_output=config["limits"]["max_output"], n=1)


class NativeBudget:
    def __init__(self, config, http, deadline, events, work):
        self.config, self.http, self.deadline = config, http, deadline
        self.events, self.work = events, Path(work)
        self.tool_calls = deque()

    def start_job(self, job_id):
        admission(self.deadline)
        self.job_id = job_id
        self.job_deadline = min(self.deadline, time.time() + self.config["limits"]["task_wall_time_seconds"])
        self.calls = 0
        self.tokens = 0
        self.input_counts = []
        self.finishes = []
        self.tool_calls.clear()
        atomic_json(self.work / "current-job.json", {"job_id": job_id, "deadline_unix": self.job_deadline})

    def context(self):
        return {"logical_call_id": f"{self.job_id}/A/turn-{self.calls+1}", "attempt": 1, "role": "A"}

    def before_call(self, payload):
        admission(self.job_deadline)
        limits = self.config["limits"]
        if self.calls >= limits["max_model_calls_per_traveler"]:
            raise RuntimeError("Native traveler model-call budget exhausted")
        if time.time() + self.config["operations"]["per_call_timeout_seconds"] >= self.job_deadline:
            raise RuntimeError("Insufficient traveler time for a complete request attempt")
        request = {key: payload[key] for key in ("model", "messages", "tools", "chat_template_kwargs") if key in payload}
        response = self.http.post(URL + "/tokenize", json=request)
        response.raise_for_status()
        counted = response.json()
        if counted["max_model_len"] != limits["context"] or type(counted["count"]) is not int:
            raise RuntimeError("Unexpected native tokenizer context or count")
        count = counted["count"]
        if count > limits["max_input_tokens"] or count + payload["max_tokens"] > limits["context"]:
            raise RuntimeError("Native input exceeds context; no truncation permitted")
        if self.tokens + count + payload["max_tokens"] > limits["total_task_tokens"]:
            raise RuntimeError("Native traveler token budget cannot reserve this whole request")
        admission(self.job_deadline)
        self.calls += 1
        self.input_counts.append(count)
        self.last_context = {"logical_call_id": f"{self.job_id}/A/turn-{self.calls}", "attempt": 1, "role": "A"}
        append_event(self.events, {"event": "model_attempt_admitted", "timestamp": timestamp(),
                                  **self.last_context, "input_tokens": count, "output_reserve": payload["max_tokens"]})

    def completed(self, response):
        raw = response.raw_response
        if raw.usage is None or raw.usage.prompt_tokens != self.input_counts[-1]:
            raise RuntimeError("Native request was rendered differently from its pre-send count")
        if raw.usage.completion_tokens > self.config["limits"]["max_output"]:
            raise RuntimeError("Native output cap violated")
        if len(raw.choices) != 1:
            raise RuntimeError("Native endpoint returned multiple completions")
        if response.content and re.search(r"<(?:tool_call|function[=>]|think>)", response.content):
            raise RuntimeError("Native parser left tool/reasoning markup in ordinary content")
        self.tokens += raw.usage.prompt_tokens + raw.usage.completion_tokens
        self.finishes.append(raw.choices[0].finish_reason)
        self.tool_calls.extend(response.tool_calls or [])
        append_event(self.events, {"event": "model_attempt_completed", "timestamp": timestamp(),
                                  **self.last_context, "usage": raw.usage.model_dump(mode="json"),
                                  "finish_reason": raw.choices[0].finish_reason})


class MeteredClient:
    def __init__(self, native, budget):
        self.native, self.budget = native, budget

    def chat_with_tools(self, *args, **kwargs):
        response = self.native.chat_with_tools(*args, **kwargs)
        self.budget.completed(response)
        return response

    def __getattr__(self, name):
        return getattr(self.native, name)


class ObservedExecutor:
    def __init__(self, native, budget, catalog_sha):
        self.native, self.budget, self.catalog_sha = native, budget, catalog_sha

    def execute(self, name, arguments):
        admission(self.budget.job_deadline)
        call = self.budget.tool_calls.popleft()
        if (call.name, call.arguments) != (name, arguments):
            raise RuntimeError("Tool execution differs from the native API response")
        identity = {**self.budget.last_context, "tool_call_id": call.id, "tool_name": name, "arguments": arguments}
        append_event(self.budget.events, {"event": "tool_started", "timestamp": timestamp(), **identity})
        started = time.monotonic()
        result = self.native.execute(name, arguments)
        append_event(self.budget.events, {"event": "tool_completed", "timestamp": timestamp(), **identity,
                                         "exact_displayed_result": result, "result_sha256": sha256(result.encode()).hexdigest(),
                                         "catalog_identity_sha256": self.catalog_sha,
                                         "duration_seconds": time.monotonic() - started,
                                         "reported_error": result.startswith("Error")})
        return result


def effective_manifest(config, agent, group_id, deadline, *, fixture=False):
    from env.env_systems.travel_planner_env.tool_schemas import TOOLS
    if fixture:
        resolved = {"execution": "offline_fixture_transport"}
    else:
        actual = read_json(WORK / "resolved-engine.json")
        resolved = {key: actual[key] for key in ("model_dtype", "kv_tensor_dtype")}
        if resolved["kv_tensor_dtype"] != config["smoke"]["result"]["actual_kv_tensor_dtype"]:
            raise RuntimeError("Native resolved KV dtype differs from the approved tested runtime")
    scientific = {
        "scope": "native_dev", "adapter_commit": os.environ["ADAPTER_COMMIT"],
        "upstream_commit": config["code"]["upstream_commit"], "upstream_patch": "none",
        "data_sha256": config["data"]["file_hashes"]["sha256"],
        "catalog_identity_sha256": config["data"]["tool_input_manifest"]["sha256"],
        "model": config["model"], "image_digest": config["runtime"]["image_digest"],
        "requested_runtime": {key: config["smoke"][key] for key in (
            "tensor_parallel_size", "max_model_len", "max_num_seqs", "kv_cache_dtype", "gpu_memory_utilization",
            "tool_call_parser", "reasoning_parser", "seed")},
        "generation": {key: config["generation"][key] for key in (
            "temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty", "chat_template_kwargs", "seed")},
        "limits": config["limits"], "attempts_per_call": config["operations"]["max_attempts"],
        "request_timeout_seconds": config["operations"]["per_call_timeout_seconds"],
        "selection": config["block_contracts"]["native_dev"]["selection"],
        "native": config["native"], "system_prompt_sha256": sha256(agent.base_messages[0]["content"].encode()).hexdigest(),
        "resolved_runtime": resolved,
        "tools_schema_sha256": fingerprint(TOOLS), "loader": "approved_pinned_jsonl_adapter",
        "environment_execution": "direct_unmodified_TravelPlannerEnvironment_step",
    }
    return {"scientific": scientific, "scientific_fingerprint": fingerprint(scientific),
            "operations": {"current_group_id": group_id, "deadline_unix": deadline,
                           "execution_stop_after": "first_group_readiness_and_memory_contract_gate",
                           "main_approval": "awaiting_main_approval"}}


def update_coverage(config, results, failure=None):
    rows = []
    for job_id in config["block_contracts"]["native_dev"]["selection"]["job_ids"]:
        if job_id in results:
            rows.append({"job_id": job_id, "status": "verified", "result": results[job_id]})
        elif failure and failure["job_id"] == job_id:
            rows.append({"job_id": job_id, "status": "failed_needs_review", "reason": failure["type"]})
        else:
            rows.append({"job_id": job_id, "status": "not_run", "reason": "awaiting_first_group_readiness_and_memory_contract"})
    atomic_json(WORK / "coverage.json", {"scope": "native_dev", "expected_jobs": 48, "jobs": rows})


def run_first_group(config, deadline, *, model_transport=None, tokenizer_transport=None):
    for identity in ("D04", "NATIVE_DEV_RUNTIME_BUDGET", "NATIVE_PINNED_DATA_READER"):
        if not any(row["id"] == identity and row["status"] == "CONFIRMED" for row in config["decision_register"]):
            raise RuntimeError(f"Unconfirmed native decision: {identity}")
    install_data(config)
    from env.env_systems.travel_env import TravelPlannerEnvironment, _parse_person_plan_from_result, SLOTS
    from run_travel import format_person_plan

    environment = TravelPlannerEnvironment({"judgement_mode": "none"})
    observation = environment.reset(seed=1)
    if observation["group_id"] != 1 or config["block_contracts"]["native_dev"]["selection"]["group_ids"] != list(range(1, 13)):
        raise RuntimeError("Native group identity differs from the approved list")
    agent = make_agent(config)
    original_client = agent.client
    base = base_actor_view(observation)
    agent.set_base_person(base["name"], base["query"], format_person_plan(base["name"], base["daily_plans"]))
    manifest = effective_manifest(config, agent, 1, deadline, fixture=model_transport is not None)
    atomic_json(WORK / "manifest.json", manifest)
    results = {}
    update_coverage(config, results)
    events = WORK / "events/group-1.jsonl"
    (WORK / "io").mkdir(mode=0o700)
    current_job = None
    try:
        save_checkpoint(WORK / "checkpoints/group-1/traveler-0", agent=agent, environment=environment,
                        group=environment.current_group, next_round=1, scientific_fingerprint=manifest["scientific_fingerprint"])
        with httpx.Client(timeout=900, trust_env=False, follow_redirects=False, transport=tokenizer_transport) as http:
            budget = NativeBudget(config, http, deadline, events, WORK)
            install_native_actor_gateway(original_client, policy=policy_for(config), journal=WireJournal(WORK / "io"),
                                         call_context=budget.context, before_call=budget.before_call, timeout_seconds=900, inner=model_transport)
            agent.client = MeteredClient(original_client, budget)
            agent.executor = ObservedExecutor(agent.executor, budget, config["data"]["tool_input_manifest"]["sha256"])
            for round_idx in (1, 2, 3, 4):
                current_job = f"native/group-1/traveler-{round_idx}"
                budget.start_job(current_job)
                task = task_actor_view(observation, round_idx)
                append_event(events, {"event": "ISSUE_TASK", "timestamp": timestamp(), "job_id": current_job, "actor_task": task})
                agent.prepare_for_person(name=task["name"], round_idx=round_idx, include_previous_plans=True,
                                         memory_context=None, memory_system=None)
                started = time.monotonic()
                action = agent.act(task["query"])
                admission(budget.job_deadline)
                ground_truth = next(answer for answer in observation["answers"] if answer["round_idx"] == round_idx)
                _, reward, info = environment.step(action, ground_truth={"name": task["name"], "daily_plans": ground_truth["daily_plans"], "judgement_mode": "none"}, need_judge=True)
                if info.get("judgement") or agent.previous_judgement:
                    raise RuntimeError("Judgement leaked into native_history")
                parsed = _parse_person_plan_from_result(action, task["name"])
                parse_valid = bool(parsed) and all(all(slot in day for slot in SLOTS) for day in parsed)
                native_result = agent.last_result
                checkpoint = save_checkpoint(WORK / f"checkpoints/group-1/traveler-{round_idx}", agent=agent,
                    environment=environment, group=environment.current_group, next_round=round_idx+1,
                    scientific_fingerprint=manifest["scientific_fingerprint"])
                result_path = Path("results") / f"group-1-traveler-{round_idx}.json"
                result = {"job_id": current_job, "scope": "native_dev", "history_mode": "native_history", "actor_task": task,
                          "final_text": action, "parsed_plan": parsed, "parse_valid": parse_valid,
                          "protocol_valid": all(reason in {"stop", "tool_calls"} for reason in budget.finishes),
                          "completion": bool(action) and budget.finishes[-1] == "stop", "native_agent_success_flag": native_result.success,
                          "native_approximate_secondary_reward": reward, "strict_two_agent_utility": "not_applicable",
                          "model_calls": budget.calls, "input_tokens_by_call": budget.input_counts,
                          "total_input_output_tokens": budget.tokens, "finish_reasons": budget.finishes,
                          "duration_seconds": time.monotonic()-started, "native_result": asdict(native_result),
                          "checkpoint_manifest": checkpoint, "scientific_fingerprint": manifest["scientific_fingerprint"]}
                atomic_json(WORK / result_path, result)
                results[current_job] = str(result_path)
                append_event(events, {"event": "traveler_verified", "timestamp": timestamp(), "job_id": current_job,
                                      "result": str(result_path), "result_fingerprint": fingerprint(result)})
                update_coverage(config, results)
                (WORK / "current-job.json").unlink()
        from .native_replay import audit_first_group
        audit_first_group(config, WORK)
        atomic_json(WORK / "status.json", {"status": "needs_memory_contract", "verified_native_jobs": 4,
                    "snapshot_after_traveler_2": True, "cache_only_replay": "passed", "main_started": False,
                    "reason": "S/C/V/P source/schema/prompts/rubric/budgets remain unconfirmed; no group 2 calls admitted"})
    except BaseException as error:
        failure = {"job_id": current_job, "type": type(error).__name__, "traceback": traceback.format_exc()}
        atomic_json(WORK / "failure.json", failure)
        update_coverage(config, results, failure)
        atomic_json(WORK / "status.json", {"status": "stopped_needs_review", "failure": failure, "main_started": False})
        raise
    finally:
        original_client.client.close()
        environment.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    config = read_json(CONFIG)
    if os.getuid() == 0:
        raise RuntimeError("Native DEV requires non-root Docker")
    deadline = min(int(os.environ["PACKAGE_DEADLINE_UNIX"]), int(os.environ["NATIVE_DEADLINE_UNIX"]))
    admission(deadline)
    if args.audit:
        from .native_replay import audit_first_group
        audit_first_group(config, WORK)
    elif args.client:
        run_first_group(config, deadline)
    else:
        from .smoke import supervise
        supervise(config, deadline, client_module="collusion_handoff.native_adapter", alias=config["model"]["served_alias"], scope="native_dev_first_group")


if __name__ == "__main__":
    main()
