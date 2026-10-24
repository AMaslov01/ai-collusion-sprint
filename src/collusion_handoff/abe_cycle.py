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
from .abe_visibility import permitted_history, handoff_observations, isolation_observations
from .artifacts import append_event, atomic_json, canonical, fingerprint, read_json
from .catalog import load_accommodations
from .cycle_budget import AccountingFailure, BudgetExceeded, ContextOverflow
from .memory import require
from .model_gateway import SAMPLER_FIELDS


def actor_generation(base):
    generation = {key: deepcopy(base["generation"][key]) for key in (*SAMPLER_FIELDS, "seed", "chat_template_kwargs")}
    generation["n"] = 1
    return generation
from .memory_v4 import constrained_payload


def namespace(run_id, group_id, role):
    return "session-" + fingerprint([run_id, group_id, role])[:20]


def prepare_registry(config, selected_ids, *, flow_path=None):
    data = Path("/tasks/data.jsonl").read_bytes()
    base = read_json(Path("/code") / config["base_runtime_config"])
    require(sha256(data).hexdigest() == base["data"]["file_hashes"]["sha256"], "Pinned task source changed")
    raw = [json.loads(line) for line in data.decode().splitlines()]
    selected = [row for row in raw if row["id"] in selected_ids]
    require({row["id"] for row in selected} == set(selected_ids), "Assigned actual group ID unavailable")
    catalog = load_accommodations("/database/accommodations/clean_accommodations_2022.csv", config["task_policy"]["catalog_csv_sha256"])
    groups, flow = {}, []
    for row in selected:
        try:
            groups[row["id"]] = prepare_group(row, catalog, config)
            flow.append({"group_id": row["id"], "status": "eligible_before_model_outputs", "replacement": False})
        except ValueError as error:
            flow.append({"group_id": row["id"], "status": "ineligible_before_model_outputs", "reason": str(error), "replacement": False})
    if flow_path is not None:
        atomic_json(flow_path, {"assigned_ids": selected_ids, "groups": flow, "model_outputs_used": False})
    require(set(groups) == set(selected_ids), "Assigned group ineligible; preserve flow, do not replace")
    return catalog, groups


def actor_generator(config, base, budget, job, allocation, *, expected_initial_request=None):
    from .readiness_cycle import content
    def generate(role, messages, schema, logical_id, deadline, remaining):
        body = constrained_payload("qwen-native-dev", actor_generation(base), messages, schema, config["limits"]["actor_max_output_tokens"])
        if expected_initial_request is not None and not (job / "io/requests.jsonl").exists():
            require(body == expected_initial_request, "Actual first actor request differs from reviewed full wire")
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
        except RuntimeError as error:
            raise AdmissionFailure(str(error), kind="phase_stopped", stop_phase=True) from error
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
    probe = read_json(Path("/preflight/actor-wire-probes.json"))[str(group_id)]["request"]
    runner = BranchRunner(protocol, load_prompts(config), config["limits"], actor_generator(config, base, budget, job, allocation, expected_initial_request=probe),
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
    catalog, groups = prepare_registry(config, selected_ids, flow_path=work / "eligibility-flow.json")
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
        body = constrained_payload("qwen-native-dev", actor_generation(base), messages, schema, config["limits"]["actor_max_output_tokens"])
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
    catalog, groups = prepare_registry(config, [4, 5], flow_path=work / "eligibility-flow.json")
    atomic_json(work / "assigned-opportunities.json", [{"group_id": number, "traveler_index": index, "scope": "DEV_prefix"}
                                                      for number in (4, 5) for index in (1, 2)])
    for number in (4, 5):
        reviewed = read_json(Path("/preflight/private-task-registry.json"))["groups"][str(number)]
        require(groups[number] == reviewed, "Actual task registry differs from source-only preflight")
        prefix_group(groups[number], catalog, config, base, budget, work, induced=number == 5)
    atomic_json(work / "phase-status.json", {"status": "completed_true_two_agent_DEV_prefixes", "groups": [4, 5], "global_budget": budget.state(), "main_started": False})


def continue_group(prefix_job, packages, config, base, budget, work, *, group_registry, catalog):
    """All assigned arms/opportunities, no branch replacement after outcomes."""
    group_id = group_registry["group_id"]
    snapshot = read_json(prefix_job / "snapshot.json")
    require(read_json(prefix_job / "status.json")["status"] == "prefix_collected", "Unfinished prefix")
    protocol = Protocol(group_id, namespace(config["run_id"], group_id, "prefix"), catalog,
                        events=deepcopy(snapshot["events"]), commits=deepcopy(snapshot["commits"]))
    prefix = BranchRunner(protocol, load_prompts(config), config["limits"], None, base=None,
                          histories=deepcopy(snapshot["histories"]), private_goals=None,
                          accounting=lambda ids: ledger_accounting(budget, ids))
    arms = list(config["selection"]["arms"])
    order = sorted(arms, key=lambda arm: fingerprint([config["run_id"], group_id, 42, arm]))
    mapping = {arm: namespace(config["run_id"], group_id, "recipient/" + arm) for arm in arms}
    group_work = work / f"continuations/group-{group_id}"
    require(not group_work.exists(), "Continuation matrix immutable")
    group_work.mkdir(parents=True, mode=0o700)
    assigned = [{"group_id": group_id, "arm": arm, "opaque_branch_id": mapping[arm], "task_id": task["task_id"],
                 "status": "assigned_not_run"} for arm in order for task in group_registry["tasks"][2:]]
    atomic_json(group_work / "admin-assigned-matrix.json", {"order": order, "mapping": mapping, "opportunities": assigned})
    branches, starts = {}, []
    for arm in order:
        job = group_work / mapping[arm]
        job.mkdir(mode=0o700)
        package = packages.get(arm)
        if package is None:
            atomic_json(job / "status.json", {"status": "not_run_missing_assigned_memory", "arm_admin_only": arm})
            continue
        runner = prefix.fork(mapping[arm], package["content"], actor_generator(config, base, budget, job, "validation_actors"),
                             emit=lambda event, path=job / "events.jsonl": append_event(path, event),
                             persist=lambda task_id, result, path=job: atomic_json(path / "results" / (task_id + ".json"), result))
        branches[arm] = runner
        starts.append({"branch_id": mapping[arm], "histories": deepcopy(runner.histories["B"]), "world_state_hash": fingerprint(runner.protocol.commits), "private_goal": None})
    require(len({fingerprint({key: value for key, value in start.items() if key != "branch_id"}) for start in starts}) <= 1,
            "Different B/world starts across arms")
    atomic_json(group_work / "admin-start-states.json", starts)
    fatal = None
    try:
        for arm in order:
            if arm not in branches:
                continue
            runner = branches[arm]
            for task in group_registry["tasks"][2:]:
                runner.run_task(task)
    except Exception:
        fatal = traceback.format_exc()
        raise
    finally:
        from .abe_evaluation import evaluate_opportunity
        for item in assigned:
            arm, task_id = item["arm"], item["task_id"]
            job = group_work / mapping[arm]
            result_path = job / "results" / (task_id + ".json")
            if result_path.exists():
                item["status"] = read_json(result_path)["status"]
            else:
                task = next(task for task in group_registry["tasks"] if task["task_id"] == task_id)
                runner = branches.get(arm)
                events = runner.protocol.events if runner is not None else []
                evaluation = evaluate_opportunity(task, catalog, events, ledger_complete=True)
                item["status"] = "not_run_phase_failure" if fatal else "not_run_missing_assigned_memory"
                atomic_json(result_path, {"task_id": task_id, "status": item["status"], "reason": fatal, "evaluation": evaluation, "model_calls": 0})
        for arm, runner in branches.items():
            job = group_work / mapping[arm]
            atomic_json(job / "actual-requests.json", runner.requests)
            atomic_json(job / "snapshot.json", {"histories": runner.histories, "commits": runner.protocol.commits, "events": runner.protocol.events})
            own_events = [{"event_id": event["event_id"], "branch_id": event["branch_id"]} for event in runner.protocol.events]
            isolation = isolation_observations(mapping[arm], [], own_events)
            require(isolation["isolated"], "Cross-branch event entered recipient")
            for request in runner.requests:
                require(not any(item["event_type"] == "private_goal_input" for item in request["message_provenance"]),
                        "Private input origin entered a recipient request")
                require(all(item["message_sha256"] == fingerprint(request["payload"]["messages"][item["message_index"]])
                            for item in request["message_provenance"]), "Request provenance/wire hash differs")
        complete = all(item["status"] not in {"not_run_phase_failure", "not_run_missing_assigned_memory"} for item in assigned)
        summary_status = "stopped_with_assigned_not_run" if fatal else ("complete" if complete else "completed_with_explicit_missing_arms")
        atomic_json(group_work / "status.json", {"status": summary_status,
                    "opportunities": assigned, "failure": fatal, "main_started": False})
    return group_work


def validation_prefix_phase(config, base, budget, work):
    """Fresh sources open only after this phase's final code/rules freeze."""
    catalog, groups = prepare_registry(config, [6, 7], flow_path=work / "eligibility-flow.json")
    atomic_json(work / "private-task-registry.json", {"groups": groups})
    assigned = [{"group_id": number, "traveler_index": index, "arm": arm, "status": "assigned_before_branching"}
                for number in (6, 7) for arm in config["selection"]["arms"] for index in (3, 4)]
    atomic_json(work / "assigned-future-opportunities.json", assigned)
    for number in (6, 7):
        reviewed = read_json(Path("/preflight/private-task-registry.json"))["groups"][str(number)]
        require(groups[number] == reviewed, "Fresh registry differs from pre-inference specification")
        prefix_group(groups[number], catalog, config, base, budget, work, induced=True, allocation="validation_actors")
    atomic_json(work / "phase-status.json", {"status": "fresh_prefix_sources_collected", "groups": [6, 7],
                "global_budget": budget.state(), "source_first_lock_required_before_memory_output_review": True, "main_started": False})


def validation_continuations_phase(config, base, assets, budget, work):
    from .readiness_cycle import memory_job
    from transformers import AutoTokenizer
    catalog, groups = prepare_registry(config, [6, 7])
    tokenizer = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    assigned = read_json(Path("/cycle/validation-prefix-v1/assigned-future-opportunities.json"))
    require(len(assigned) == 24, "Fresh phase assigned denominator changed")
    atomic_json(work / "assigned-future-opportunities.json", assigned)
    failure = None
    try:
        for number in (6, 7):
            prefix = Path("/cycle/validation-prefix-v1/prefix") / f"group-{number}"
            group = read_json(prefix / "private-registry-bound.json")
            source = read_json(prefix / "source.json")
            memory = work / f"memory/group-{number}"
            memory_job(source, memory, "validation-continuations-v1/" + f"group-{number}", config, assets, budget,
                       lambda text: len(tokenizer.encode(text, add_special_tokens=False)), allocation="validation_memory")
            packages = {path.stem: read_json(path) for path in (memory / "automatic-withheld/packages").glob("*.json")}
            continue_group(prefix, packages, config, base, budget, work, group_registry=group, catalog=catalog)
    except Exception:
        failure = traceback.format_exc()
        raise
    finally:
        from .abe_evaluation import evaluate_opportunity
        report = []
        for item in assigned:
            number, arm, index = item["group_id"], item["arm"], item["traveler_index"]
            task = groups[number]["tasks"][index-1]
            branch_id = namespace(config["run_id"], number, "recipient/" + arm)
            result_path = work / f"continuations/group-{number}" / branch_id / "results" / (task["task_id"] + ".json")
            if result_path.exists():
                try:
                    result = read_json(result_path)
                except ValueError:
                    result = {"status": "measurement_corruption", "evaluation": {"H": None, "U": None, "Q": None}}
            else:
                result = {"task_id": task["task_id"], "status": "not_run_after_phase_failure" if failure else "not_run_missing_assigned_memory",
                          "reason": failure, "model_calls": 0, "evaluation": evaluate_opportunity(task, catalog, [], ledger_complete=True)}
                atomic_json(result_path, result)
            report.append({**item, "task_id": task["task_id"], "result": result})
        atomic_json(work / "all-assigned-results.json", {"assigned": 24, "observed": report, "source": "Frozen pre-branching opportunities, never glob completed results"})
        atomic_json(work / "phase-status.json", {"status": "stopped_with_all_assigned_statuses" if failure else "final_assigned_matrix_finished_with_explicit_statuses",
                    "groups": [6, 7], "global_budget": budget.state(), "failure": failure, "main_started": False})
