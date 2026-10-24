"""Bounded A/B prefixes and six isolated recipient branches for DEV only."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import time
import traceback

import httpx

from .abe_protocol import Protocol, action_schema, public_task
from .abe_runner import AdmissionFailure, BranchRunner, ServiceFailure, TaskLimitExceeded
from .abe_tasks import prepare_group
from .artifacts import append_event, atomic_json, canonical, fingerprint, read_json
from .catalog import load_accommodations
from .cycle_budget import AccountingFailure, BudgetExceeded, ContextOverflow
from .memory import require
from .memory_v4 import constrained_payload


def namespace(run_id, group_id, role):
    return "session-" + fingerprint([run_id, group_id, role])[:20]


def prepare_registry(config, selected_ids):
    data = Path("/tasks/data.jsonl").read_bytes()
    base = read_json(Path("/code") / config["base_runtime_config"])
    require(sha256(data).hexdigest() == base["data"]["file_hashes"]["sha256"], "Pinned task source changed")
    raw = [json.loads(line) for line in data.decode().splitlines()]
    selected = [row for row in raw if row["id"] in selected_ids]
    require({row["id"] for row in selected} == set(selected_ids), "Assigned actual group ID unavailable")
    catalog = load_accommodations("/database/accommodations/clean_accommodations_2022.csv", config["task_policy"]["catalog_csv_sha256"])
    groups = {row["id"]: prepare_group(row, catalog, config) for row in selected}
    return catalog, groups


def actor_generator(config, base, budget, job, allocation):
    from .readiness_cycle import content
    def generate(role, messages, schema, logical_id, deadline, remaining):
        body = constrained_payload("qwen-native-dev", base["generation"], messages, schema, config["limits"]["actor_max_output_tokens"])
        probe_file = Path("/preflight/actor-wire-probes.json")
        if "/A/cycle-1/call-1" in logical_id and budget.state()["attempts"] == 0 and probe_file.exists():
            matches = [value for value in read_json(probe_file).values() if value["request"] == body]
            require(len(matches) == 1, "Actual first actor request differs from reviewed full wire")
        try:
            count = budget.preview(body, deadline=deadline)
            if count + body["max_tokens"] > remaining:
                raise TaskLimitExceeded("Task cannot reserve the complete input/output request")
            response = budget.dispatch(body, logical_id, allocation, job=job, deadline=deadline, prepared=True)
            text = content(response)
            usage = response.json()["usage"]
            return {"text": text, "input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"]}
        except ContextOverflow as error:
            raise AdmissionFailure(str(error), kind="context_overflow") from error
        except BudgetExceeded as error:
            raise AdmissionFailure(str(error), kind="budget_exhausted", stop_phase=True) from error
        except httpx.TimeoutException as error:
            raise ServiceFailure(str(error), kind="timeout") from error
        except httpx.HTTPError as error:
            raise ServiceFailure(str(error), kind="model_service_failure") from error
        except AccountingFailure as error:
            raise ServiceFailure(str(error), kind="accounting_failure") from error
    return generate


def ledger_accounting(budget, logical_ids):
    from .memory import strict_json
    requests = [strict_json(line) for line in (budget.ledger / "requests.jsonl").read_text().splitlines()] if (budget.ledger / "requests.jsonl").exists() else []
    responses = [strict_json(line) for line in (budget.ledger / "responses.jsonl").read_text().splitlines()] if (budget.ledger / "responses.jsonl").exists() else []
    by_id = {item["logical_call_id"]: item for item in responses}
    selected = [item for item in requests if item["logical_call_id"] in logical_ids]
    unknown, tokens = [], 0
    for request in selected:
        result = by_id.get(request["logical_call_id"], {})
        actual = result.get("accounted_actual_tokens")
        tokens += request["reserved_input_output_tokens"] if actual is None else actual
        if actual is None:
            unknown.append(request["logical_call_id"])
    return {"attempts": len(selected), "accounted_input_output_tokens": tokens, "unknown_spend_requests": unknown}


def load_prompts(config):
    return {role: (Path("/code") / config["protocol"][role + "_prompt"]).read_text() for role in ("A", "B")}


def prefix_group(group, catalog, config, base, budget, work, *, induced, allocation="developmental_prefixes"):
    group_id = group["group_id"]
    job = work / f"prefix/group-{group_id}"
    require(not job.exists(), "Prefix job immutable; never regenerate")
    job.mkdir(parents=True, mode=0o700)
    atomic_json(job / "private-registry.json", group)
    event_file = job / "events.jsonl"
    protocol = Protocol(group_id, namespace(config["run_id"], group_id, "prefix"), catalog,
                        emit=lambda event: append_event(event_file, event))
    private = {role: (Path("/code") / config["protocol"]["private_" + role + "_prompt"]).read_text() for role in ("A", "B")} if induced else {}
    private_identity = {role: {"origin_event_id": protocol.branch_id + "/private-input-" + role, "content_sha256": sha256(text.encode()).hexdigest()}
                        for role, text in private.items()}
    atomic_json(job / "private-goal-origins.json", private_identity)
    runner = BranchRunner(protocol, load_prompts(config), config["limits"], actor_generator(config, base, budget, job, allocation),
                          base=group["base_person"], private_goals=private,
                          persist=lambda task_id, result: atomic_json(job / "results" / (task_id + ".json"), result),
                          accounting=lambda ids: ledger_accounting(budget, ids))
    failure = None
    try:
        for task in group["tasks"][:2]:
            runner.run_task(task)
    except Exception:
        failure = traceback.format_exc()
        raise
    finally:
        for task in group["tasks"][:2]:
            if not (job / "results" / (task["task_id"] + ".json")).exists():
                atomic_json(job / "results" / (task["task_id"] + ".json"), {"task_id": task["task_id"], "status": "not_run_after_phase_failure", "reason": failure})
        atomic_json(job / "status.json", {"status": "prefix_collected" if failure is None else "failed", "induced": induced, "failure": failure})
        atomic_json(job / "snapshot.json", {"histories": runner.histories, "commits": protocol.commits, "events": protocol.events,
                    "world_state_sha256": fingerprint(protocol.commits), "B_historical_sha256": fingerprint(runner.histories["B"]),
                    "private_goal_origins": private_identity, "checkpoint_after": 2})
        source = runner.source({"catalog_version": config["task_policy"]["catalog_csv_sha256"], "code_commit": os.environ["ADAPTER_COMMIT"]})
        atomic_json(job / "source.json", source)
        atomic_json(job / "actual-requests.json", runner.requests)
        for task in group["tasks"][2:]:
            for ref in task["memory"]["source_refs"]:
                delivered = [event for event in source["archive"]["events"] if event["event_type"] == "issued_task"
                             and event["traveler_idx"] == 1]
                require(len(delivered) == 1, "Cannot bind Q to a delivered prefix query")
                actual_query = json.loads(delivered[0]["content"])["current_public_task"]["source_query"]
                require(sha256(actual_query.encode()).hexdigest() == ref["query_sha256"] and actual_query.encode()[ref["start"]:ref["end"]].decode() == ref["quote"], "Q source was not actually delivered")
                ref["delivered_origin_event_id"] = delivered[0]["event_id"]
        atomic_json(job / "private-registry-bound.json", group)
    return job


def actor_preflight(config, base, assets, work, selected_ids):
    """Actual initial action requests and source registry; no model calls."""
    from transformers import AutoTokenizer
    catalog, groups = prepare_registry(config, selected_ids)
    tokenizer = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    probes = {}
    for group_id, group in groups.items():
        protocol = Protocol(group_id, namespace(config["run_id"], group_id, "prefix"), catalog)
        private = {role: (Path("/code") / config["protocol"]["private_" + role + "_prompt"]).read_text()
                   for role in ("A", "B")} if group_id != 4 else {}
        runner = BranchRunner(protocol, load_prompts(config), config["limits"], None, base=group["base_person"], private_goals=private)
        task = group["tasks"][0]
        protocol.issue(task)
        for role in ("A", "B"):
            runner.add(role, "user", "issued_task", canonical({"current_public_task": public_task(task)}).decode(), "user")
        schema = action_schema("A", tools_allowed=True)
        messages = runner.messages("A", schema)
        body = constrained_payload("qwen-native-dev", base["generation"], messages, schema, config["limits"]["actor_max_output_tokens"])
        count = len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False))
        require(count + body["max_tokens"] <= 32768, "Initial actor request cannot fit")
        probes[str(group_id)] = {"request": body, "request_sha256": fingerprint(body), "rendered_input_tokens": count}
    atomic_json(work / "private-task-registry.json", {"groups": groups, "main_started": False})
    atomic_json(work / "actor-wire-probes.json", probes)
    atomic_json(work / "actor-preflight.json", {"status": "passed_no_model_calls", "groups": selected_ids,
                "scope": "True typed A/B prefixes, no fresh groups opened outside assigned IDs", "new_model_calls": 0,
                "registry_sha256": sha256((work / "private-task-registry.json").read_bytes()).hexdigest(),
                "probe_sha256": sha256((work / "actor-wire-probes.json").read_bytes()).hexdigest()})


def prefix_phase(config, base, budget, work):
    catalog, groups = prepare_registry(config, [4, 5])
    atomic_json(work / "assigned-opportunities.json", [{"group_id": number, "traveler_index": index, "scope": "DEV_prefix"}
                                                      for number in (4, 5) for index in (1, 2)])
    for number in (4, 5):
        reviewed = read_json(Path("/preflight/private-task-registry.json"))["groups"][str(number)]
        require(groups[number] == reviewed, "Actual task registry differs from source-only preflight")
        prefix_group(groups[number], catalog, config, base, budget, work, induced=number == 5)
    atomic_json(work / "phase-status.json", {"status": "completed_true_two_agent_DEV_prefixes", "groups": [4, 5], "global_budget": budget.state(), "main_started": False})
