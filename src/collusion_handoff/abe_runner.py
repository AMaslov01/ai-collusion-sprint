"""Recipient histories and bounded live/fixture A/B task orchestration."""

from copy import deepcopy
from hashlib import sha256
import json
import time

from jsonschema import ValidationError

from .abe_evaluation import evaluate_opportunity
from .abe_protocol import Protocol, action_schema, bind_review_schema, public_task, validate_action
from .abe_tools import execute_tool
from .abe_visibility import historical_messages, permitted_history
from .artifacts import canonical, fingerprint
from .memory import MemoryError, require, strict_json


class ServiceFailure(RuntimeError):
    """Stop the phase on a model-service/transport failure; never retry."""
    def __init__(self, message, *, kind="model_service_failure"):
        super().__init__(message)
        self.kind = kind


class AdmissionFailure(RuntimeError):
    def __init__(self, message, *, kind, stop_phase=False):
        super().__init__(message)
        self.kind, self.stop_phase = kind, stop_phase


class TaskLimitExceeded(MemoryError):
    pass


class BranchRunner:
    def __init__(self, protocol, prompts, limits, generate, *, base, histories=None, archive=None, private_goals=None, persist=None, accounting=None):
        self.protocol, self.prompts, self.limits = protocol, prompts, limits
        self.generate, self.persist = generate, persist
        self.accounting = accounting
        self.histories = deepcopy(histories) if histories is not None else {"A": [], "B": []}
        self.archive, self.private_goals = archive, private_goals or {}
        self.receipts = {role: set() for role in ("A", "B")}
        for role in ("A", "B"):
            for record in self.histories[role]:
                if record.get("receipt_id"):
                    self.receipts[role].add(record["receipt_id"])
        if histories is None:
            for role in ("A", "B"):
                self.add(role, "user", "base_query", canonical({"base_name": base["name"], "base_query": base["query"]}).decode(), "user")
                self.add(role, "user", "provided_base_plan", canonical({"base_name": base["name"], "daily_plans": base["daily_plans"]}).decode(), "user")
        self.requests = []

    def add(self, recipient, message_role, kind, content, source_role, *, receipt_id=None):
        event = self.protocol.event("actor_delivery", "runner", {"recipient": recipient, "message_role": message_role,
                                      "source_role": source_role, "origin_type": kind, "content": content})
        record = {"origin_event_id": event["event_id"], "event_type": kind, "message_role": message_role,
                  "role": source_role, "content": content, "order": len(self.histories[recipient]),
                  "traveler_idx": self.protocol.task["traveler_index"] if self.protocol.task else 0,
                  "turn_idx": len(self.histories[recipient])}
        if receipt_id is not None:
            record["receipt_id"] = receipt_id
            self.receipts[recipient].add(receipt_id)
        self.histories[recipient].append(record)
        return record

    def messages(self, role, schema):
        result = [{"role": "system", "content": self.prompts[role]}]
        if role in self.private_goals:
            result.append({"role": "user", "content": self.private_goals[role]})
        if role == "A" and self.archive is not None:
            result.append({"role": "user", "content": "[PRIVATE ARCHIVE]\n" + self.archive + "\n[/PRIVATE ARCHIVE]"})
        result.extend(historical_messages(self.histories[role]))
        result.append({"role": "user", "content": "Return one action under this response schema: " + canonical(schema).decode()})
        return result

    def message_provenance(self, role, messages):
        origins = [{"event_type": "current_nominal_role", "origin_event_id": "nominal-role/" + role}]
        if role in self.private_goals:
            origins.append({"event_type": "private_goal_input", "origin_event_id": self.protocol.branch_id + "/private-input-" + role})
        if role == "A" and self.archive is not None:
            origins.append({"event_type": "selected_historical_archive", "origin_event_id": "selected-archive"})
        origins.extend({"event_type": record["event_type"], "origin_event_id": record["origin_event_id"]} for record in self.histories[role])
        origins.append({"event_type": "current_action_schema", "origin_event_id": "current-action-schema"})
        require(len(origins) == len(messages), "Message provenance shape differs from actual wire")
        return [{**origin, "message_index": index, "message_sha256": fingerprint(message)}
                for index, (origin, message) in enumerate(zip(origins, messages))]

    def run_task(self, task):
        self.protocol.issue(task)
        for role in ("A", "B"):
            self.add(role, "user", "issued_task", canonical({"current_public_task": public_task(task)}).decode(), "user")
        calls, tokens, tools = 0, 0, 0
        deadline = time.time() + self.limits["task_wall_seconds"]
        first_proposal = None
        failure = None
        fatal = None
        logical_ids = []
        try:
            while self.protocol.terminal is None:
                role = self.protocol.expected_role
                allowance = self.limits[f"{role}_tool_requests_per_cycle"]
                used = 0
                while True:
                    if calls >= self.limits["task_max_calls"]:
                        raise TaskLimitExceeded("Task model-call budget exhausted")
                    if time.time() >= deadline:
                        raise AdmissionFailure("Task wall-time exhausted", kind="timeout")
                    schema = action_schema(role, tools_allowed=used < allowance)
                    if role == "B":
                        schema = bind_review_schema(schema, self.protocol.proposal)
                    messages = self.messages(role, schema)
                    logical_id = f"{self.protocol.branch_id}/{task['task_id']}/{role}/cycle-{self.protocol.cycle}/call-{calls+1}"
                    logical_ids.append(logical_id)
                    self.protocol.event("actor_request", role, {"logical_call_id": logical_id, "messages_sha256": fingerprint(messages),
                                        "schema_sha256": fingerprint(schema), "initial_A_prime_candidate": role == "A" and first_proposal is None})
                    self.requests.append({"role": role, "task_id": task["task_id"], "payload": {"messages": deepcopy(messages)},
                                          "message_provenance": self.message_provenance(role, messages), "logical_call_id": logical_id})
                    # The real generator reserves input+4096 against this remaining budget before HTTP dispatch.
                    completion = self.generate(role, messages, schema, logical_id, deadline,
                                               self.limits["task_max_input_output_tokens"] - tokens)
                    calls += 1
                    tokens += completion["input_tokens"] + completion["output_tokens"]
                    require(completion["output_tokens"] <= self.limits["actor_max_output_tokens"] and
                            tokens <= self.limits["task_max_input_output_tokens"], "Actual task token budget exceeded")
                    action = strict_json(completion["text"])
                    validate_action(action, role, tools_allowed=used < allowance)
                    self.add(role, "assistant", "assistant_action", completion["text"], role)
                    if action["type"] == "tool_request":
                        require(tools < self.limits["task_max_tool_receipts"], "Task tool receipt limit exceeded")
                        try:
                            result = execute_tool(self.protocol, action["tool"], action["arguments"], page_size=self.limits["catalog_page_size"])
                        except Exception as error:
                            raise AdmissionFailure(str(error), kind="tool_error") from error
                        event = self.protocol.event("tool_completed", role, {"tool": action["tool"], "arguments": action["arguments"], "result": result})
                        receipt = event["event_id"] + "/receipt"
                        self.add(role, "user", "tool_result", canonical({"receipt_id": receipt, "tool": action["tool"], "result": result}).decode(), "tool", receipt_id=receipt)
                        used += 1
                        tools += 1
                        continue
                    refs = [] if action["type"] == "refusal" else (action["evidence_refs"] if role == "B" else [ref for slot in action["plan"]["slots"] for ref in slot["evidence_refs"]])
                    require(set(refs) <= self.receipts[role], "Unobserved tool receipt citation")
                    result = self.protocol.receive(role, action)
                    if action["type"] == "refusal":
                        peer = "B" if role == "A" else "A"
                        self.add(peer, "user", "peer_message", canonical({"from": role, "refusal": action}).decode(), role)
                    elif role == "A":
                        first_proposal = first_proposal or deepcopy(result)
                        self.add("B", "user", "peer_message", canonical({"from": "A", "proposal": result}).decode(), "A")
                    else:
                        self.add("A", "user", "peer_message", canonical({"from": "B", "review": action}).decode(), "B")
                        if self.protocol.terminal == "committed":
                            for recipient in ("A", "B"):
                                self.add(recipient, "user", "commit_receipt", canonical({"runner_commit": result}).decode(), "runner")
                    break
        except (ServiceFailure, AdmissionFailure) as error:
            failure = {"type": type(error).__name__, "kind": error.kind, "message": str(error)}
            if self.protocol.terminal is None:
                self.protocol.finish(error.kind)
            if isinstance(error, ServiceFailure) or error.stop_phase:
                fatal = error
        except (MemoryError, ValidationError, json.JSONDecodeError, ValueError) as error:
            failure = {"type": type(error).__name__, "message": str(error)}
            if self.protocol.terminal is None:
                self.protocol.finish("budget_exhausted" if isinstance(error, TaskLimitExceeded) else "protocol_invalid")
        evaluation = evaluate_opportunity(task, self.protocol.catalog, self.protocol.events, ledger_complete=True)
        spend = self.accounting(logical_ids) if self.accounting else {"attempts": calls, "accounted_input_output_tokens": tokens, "unknown_spend_requests": []}
        result = {"task_id": task["task_id"], "status": self.protocol.terminal, "model_calls": spend["attempts"],
                  "input_output_tokens": spend["accounted_input_output_tokens"], "unknown_spend_requests": spend["unknown_spend_requests"],
                  "tool_receipts": tools, "failure": failure,
                  "initial_proposal": first_proposal, "evaluation": evaluation,
                  "world_state_hash": fingerprint(self.protocol.commits)}
        if self.persist:
            self.persist(task["task_id"], result)
        self.last_result = result
        if fatal is not None:
            raise fatal
        return result

    def source(self, versions):
        records = permitted_history(self.histories["A"])
        events = []
        for index, record in enumerate(records):
            events.append({"event_id": record["origin_event_id"], "origin_event_id": record["origin_event_id"],
                           "role": record["role"], "event_type": record["event_type"], "group_id": self.protocol.group_id,
                           "traveler_idx": record["traveler_idx"], "turn_idx": record["turn_idx"], "order": index,
                           "content": record["content"], "content_sha256": sha256(record["content"].encode()).hexdigest(),
                           "derived_from": [], "source_version": deepcopy(versions)})
        archive = {"schema_version": "abe_permitted_source_v4", "checkpoint_id": f"group{self.protocol.group_id}-after-traveler2",
                   "source_policy_id": "two_agent_permitted_work_no_private_inputs_v4", "events": events,
                   "allowed_commit_event_types": ["commit_receipt"]}
        return {"archive": archive, "archive_sha256": fingerprint(archive)}

    def fork(self, branch_id, archive, generate, *, emit=None, persist=None):
        protocol = self.protocol.fork(branch_id, emit=emit)
        b_history = permitted_history(self.histories["B"])
        a_base = [record for record in permitted_history(self.histories["A"]) if record["event_type"] in {"base_query", "provided_base_plan"}]
        return BranchRunner(protocol, self.prompts, self.limits, generate, base=None,
                            histories={"A": a_base, "B": b_history}, archive=archive,
                            private_goals=None, persist=persist, accounting=self.accounting)
