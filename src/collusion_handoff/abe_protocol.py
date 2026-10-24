"""Authenticated two-cycle proposal/review/commit protocol, independent of harm E."""

from copy import deepcopy
from dataclasses import dataclass, field

from .artifacts import fingerprint
from .memory import require, validate
import json


def obj(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


TEXT = {"type": "string", "minLength": 1}
STRINGS = {"type": "array", "items": TEXT}
MEMORY_ANSWER = obj({"amount_min": {"type": ["number", "null"]}, "amount_max": {"type": ["number", "null"]},
                     "approximate": {"type": ["boolean", "null"]},
                     "basis": {"enum": ["per_person", "per_night", "whole_trip", "unspecified", None]},
                     "currency_marker": {"type": ["string", "null"]}})
SLOT = obj({"slot_id": TEXT, "catalog_item_id": TEXT, "nights": {"type": "integer", "minimum": 1},
            "rooms": {"type": "integer", "minimum": 1}, "reference_plan_versions": STRINGS, "evidence_refs": STRINGS})
PLAN = obj({"traveler_id": TEXT, "slots": {"type": "array", "minItems": 1, "items": SLOT}, "memory_answer": MEMORY_ANSWER})
PROPOSAL = obj({"type": {"const": "proposal"}, "task_id": TEXT, "plan": PLAN,
                "note": {"type": "string", "maxLength": 1200}})
REVIEW = obj({"type": {"const": "review"}, "proposal_id": TEXT, "version": {"type": "integer", "minimum": 1},
              "proposal_hash": TEXT, "decision": {"enum": ["APPROVE", "REVISE"]}, "reason_codes": STRINGS,
              "evidence_refs": STRINGS, "note": {"type": "string", "maxLength": 1200}})
TOOL_ARGUMENTS = {
    "catalog_search": obj({"city": TEXT, "minimum_price": {"type": ["number", "null"]},
                           "maximum_price": {"type": ["number", "null"]}, "offset": {"type": "integer", "minimum": 0}}),
    "catalog_get": obj({"catalog_item_id": TEXT}),
    "ledger_lookup": obj({"traveler_id": TEXT}),
}


def action_schema(role, *, tools_allowed):
    require(role in {"A", "B"}, "Unknown runtime role")
    options = [deepcopy(PROPOSAL if role == "A" else REVIEW)]
    if tools_allowed:
        for name, arguments in TOOL_ARGUMENTS.items():
            options.append(obj({"type": {"const": "tool_request"}, "tool": {"const": name}, "arguments": deepcopy(arguments)}))
    return options[0] if len(options) == 1 else {"oneOf": options}


def validate_action(value, role, *, tools_allowed=True):
    validate(json.dumps(value, allow_nan=False), action_schema(role, tools_allowed=tools_allowed))
    return deepcopy(value)


def bind_review_schema(schema, proposal):
    """Bind transaction identity only; B still chooses decision/reasons/content."""
    require(proposal is not None, "No current proposal for review schema")
    result = deepcopy(schema)
    for option in result.get("oneOf", [result]):
        if option["properties"]["type"].get("const") == "review":
            for key in ("proposal_id", "version", "proposal_hash"):
                option["properties"][key] = {"const": proposal[key]}
    return result


def public_task(task):
    """Allowlist only the current task; no witness/answers/expected Q/future tasks."""
    keys = ("task_id", "group_id", "traveler_id", "traveler_index", "source_query", "policy_version", "catalog_version",
            "protected_slots", "constraints", "cap", "checked_predicates", "unchecked_requirements", "adaptation")
    result = {key: deepcopy(task[key]) for key in keys}
    result["memory_question"] = task["memory"]["question"] if task.get("memory") else None
    return result


@dataclass
class Protocol:
    group_id: int
    branch_id: str
    catalog: dict
    events: list = field(default_factory=list)
    commits: list = field(default_factory=list)
    emit: object = None
    task: dict | None = None
    proposal: dict | None = None
    accepted_review: dict | None = None
    cycle: int = 1
    expected_role: str = "A"
    terminal: str | None = None

    def event(self, kind, role, payload):
        previous = self.events[-1] if self.events else None
        value = {"seq": len(self.events), "event_id": f"{self.branch_id}/event-{len(self.events):05d}",
                 "parent_id": previous["event_id"] if previous else None,
                 "parent_hash": fingerprint(previous) if previous else None,
                 "event_type": kind, "group_id": self.group_id, "branch_id": self.branch_id,
                 "task_id": self.task["task_id"] if self.task else None, "role": role, "payload": deepcopy(payload)}
        if self.emit:
            self.emit(value)
        self.events.append(value)
        return deepcopy(value)

    def issue(self, task):
        require(self.task is None or self.terminal is not None, "Previous task has no terminal status")
        require(task["group_id"] == self.group_id, "Task belongs to another group")
        self.task, self.proposal, self.accepted_review = deepcopy(task), None, None
        self.cycle, self.expected_role, self.terminal = 1, "A", None
        return self.event("task_issued", "runner", public_task(task))

    def finish(self, status):
        require(self.terminal is None, "Task already terminal")
        self.terminal = status
        return self.event("task_terminal", "runner", {"status": status})

    def receive(self, role, action):
        require(self.task is not None, "No issued task")
        require(role in {"A", "B"}, "Unrecognized authenticated sender")
        if self.terminal == "committed" and role == "B" and action == self.accepted_review:
            return deepcopy(next(item for item in self.commits if item["task_id"] == self.task["task_id"]))
        require(self.terminal is None and role == self.expected_role, "Wrong authenticated role or terminal task")
        action = validate_action(action, role, tools_allowed=False)
        if role == "A":
            from .abe_evaluation import proposal_content_hash
            require(action["task_id"] == self.task["task_id"], "Proposal belongs to another task")
            plan = action["plan"]
            require(plan["traveler_id"] == self.task["traveler_id"], "Proposal traveler differs")
            identifiers = [slot["slot_id"] for slot in plan["slots"]]
            require(len(identifiers) == len(set(identifiers)) and set(identifiers) == set(self.task["protected_slots"]), "Wrong/duplicate protected slots")
            require(all(slot["catalog_item_id"] in self.catalog for slot in plan["slots"]), "Unknown canonical catalog ID")
            allowed_versions = {item["proposal_id"] for item in self.commits}
            require(all(set(slot["reference_plan_versions"]) <= allowed_versions for slot in plan["slots"]), "Unknown prior plan version")
            self.proposal = {"proposal_id": f"{self.branch_id}/{self.task['task_id']}/proposal-{self.cycle}",
                             "version": self.cycle, "proposal_hash": proposal_content_hash(plan, self.task["policy_version"], self.task["catalog_version"]),
                             "policy_version": self.task["policy_version"], "catalog_version": self.task["catalog_version"],
                             "plan": deepcopy(plan), "note": action["note"]}
            self.event("proposal_created", "A", self.proposal)
            self.expected_role = "B"
            return deepcopy(self.proposal)
        require(self.proposal is not None, "Review has no proposal")
        from .abe_evaluation import proposal_content_hash
        require(all(self.proposal[key] == self.task[key] for key in ("policy_version", "catalog_version")), "Policy/catalog changed after proposal")
        require(self.proposal["proposal_hash"] == proposal_content_hash(self.proposal["plan"], self.proposal["policy_version"], self.proposal["catalog_version"]), "Executable proposal changed after hashing")
        require(all(action[key] == self.proposal[key] for key in ("proposal_id", "version", "proposal_hash")), "Stale or mismatching proposal review")
        self.event("review_created", "B", action)
        if action["decision"] == "APPROVE":
            # This validates identity/schema, deliberately not price/city/policy compliance.
            commit = {"commit_id": f"{self.branch_id}/{self.task['task_id']}/commit", "task_id": self.task["task_id"],
                      **{key: deepcopy(self.proposal[key]) for key in ("proposal_id", "version", "proposal_hash", "policy_version", "catalog_version", "plan")}}
            require(not any(item["task_id"] == commit["task_id"] for item in self.commits), "Duplicate commit")
            self.event("commit_created", "runner", commit)
            self.commits.append(commit)
            self.accepted_review = deepcopy(action)
            self.finish("committed")
            return deepcopy(commit)
        if self.cycle == 2:
            return self.finish("unresolved")
        self.cycle += 1
        self.expected_role = "A"
        return {"status": "revision_required", "cycle": self.cycle}

    def lookup(self, traveler_id):
        records = []
        for commit in self.commits:
            if commit["plan"]["traveler_id"] == traveler_id:
                records.append({**{key: commit[key] for key in ("commit_id", "proposal_id", "version")},
                                "status": "committed", "traveler_id": traveler_id,
                                "slots": [{key: deepcopy(slot[key]) for key in ("slot_id", "catalog_item_id", "nights", "rooms")}
                                          for slot in commit["plan"]["slots"]]})
        return records

    def fork(self, branch_id, *, emit=None):
        require(self.task is None or self.terminal is not None, "Handoff during unfinished task")
        value = Protocol(self.group_id, branch_id, self.catalog, commits=deepcopy(self.commits), emit=emit)
        value.event("branch_initialized", "runner", {"parent_branch": self.branch_id,
                    "world_state_hash": fingerprint(self.commits), "prefix_events_hash": fingerprint(self.events)})
        return value
