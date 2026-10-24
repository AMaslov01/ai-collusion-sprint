"""Additional calibration ceilings on the unchanged append-only DEV ledger."""

from hashlib import sha256
from pathlib import Path
import re

from .abe_cycle import namespace
from .artifacts import append_event, read_json
from .cycle_budget import BudgetExceeded, CycleBudget
from .memory import require, strict_json


def journal(path):
    data = path.read_bytes() if path.exists() else b""
    require(not data or data.endswith(b"\n"), "Truncated calibration accounting journal")
    return [strict_json(line) for line in data.decode().splitlines()]


def verify_parent(root, closure, profile):
    root = Path(root)
    require(closure["status"] == "closed_model_calls_and_cache_checks" and closure["main_allowed"] is False,
            "Audited closed DEV parent required")
    require(sha256((root / "master-manifest.json").read_bytes()).hexdigest() == closure["master_manifest_sha256"],
            "Original DEV master changed")
    master = read_json(root / "master-manifest.json")
    require(master["limits"] == closure["limits"] == profile["limits"] and
            master["allocations"] == closure["allocations"] == profile["allocations"],
            "Global DEV limits or allocations were reset/expanded")
    for name, seal in closure["ledger_prefix"].items():
        data = (root / "ledger" / name).read_bytes()
        prefix = data[:seal["bytes"]]
        require(len(prefix) == seal["bytes"] and sha256(prefix).hexdigest() == seal["sha256"] and
                prefix.endswith(b"\n") and len(prefix.splitlines()) == seal["rows"],
                "Closed parent request/response byte prefix changed")
    require(closure["before"]["unknown_spend_requests"] == [], "Unknown parent spend cannot authorize calibration")
    return master


class CalibrationBudget(CycleBudget):
    """Every generation is charged once globally, with stricter child ceilings.

    The child claims journal records the exact authorized logical namespace before
    dispatch. It is not a replacement spending ledger and does not reset counters.
    """
    def __init__(self, root, profile, http, *, phase_work, amendment, closure, phase, selected_worlds,
                 handoff_world=None, clock=None):
        kwargs = {"clock": clock} if clock is not None else {}
        super().__init__(root, profile, http, phase_work=phase_work, **kwargs)
        self.amendment, self.closure, self.phase = amendment, closure, phase
        self.selected_worlds, self.handoff_world = list(selected_worlds), handoff_world
        require(phase in amendment["phases"].values() and len(self.selected_worlds) == 2 and
                len(set(self.selected_worlds)) == 2 and set(self.selected_worlds) <= set(amendment["source_frame"]["ordered_ids"]),
                "Unknown phase or source-only fixed world assignment")
        require(handoff_world is None or handoff_world in self.selected_worlds, "Unassigned handoff source")
        self.claims_path = Path(root) / amendment["run_id"] / "accounting/claims.jsonl"
        verify_parent(root, closure, profile)
        require(amendment["charges"] == {"prefix_actors": "validation_actors", "memory_builders": "reviewed_targeted_repairs",
                                         "continuation_actors": "validation_actors"}, "Unreviewed calibration charge categories")
        ceilings = amendment["child_limits"]
        require(ceilings["prefix_actor_calls"] + ceilings["memory_builder_calls"] + ceilings["continuation_actor_calls"] ==
                ceilings["model_attempts"], "Child ceilings disagree")
        require(closure["before"]["attempts"] + ceilings["model_attempts"] <= profile["limits"]["max_new_model_attempts"] and
                closure["before"]["input_output_tokens"] + ceilings["input_output_tokens"] <= profile["limits"]["max_input_output_tokens"] and
                closure["before"]["allocated_gpu_container_seconds"] + ceilings["gpu_container_seconds"] <= profile["limits"]["gpu_service_wall_seconds"],
                "Calibration would exceed the unchanged global DEV allowance")
        require(ceilings["prefix_actor_calls"] + ceilings["continuation_actor_calls"] <=
                closure["remaining_allocations"]["validation_actors"] and ceilings["memory_builder_calls"] <=
                closure["remaining_allocations"]["reviewed_targeted_repairs"], "Original stage allocation lacks room")

    def classify(self, identity, allocation, phase):
        stages = self.amendment["phases"]
        if phase == stages["handoff"] and identity.startswith(phase + "/group-"):
            require(self.handoff_world is not None and identity in {
                f"{phase}/group-{self.handoff_world}/{stage}" for stage in ("S", "C", "V", "P")},
                "Memory call is outside the selected source/stages")
            kind = "memory_builders"
        else:
            parts = identity.split("/")
            require(len(parts) == 5 and parts[2] in {"A", "B"} and re.fullmatch(r"cycle-[12]", parts[3]) and
                    re.fullmatch(r"call-[1-9][0-9]*", parts[4]), "Unknown calibration actor logical identity")
            match = re.fullmatch(r"g([0-9]+)-t([1-4])", parts[1])
            require(match is not None, "Unknown assigned calibration task")
            number, index = map(int, match.groups())
            require(parts[1] == f"g{number}-t{index}" and int(parts[4].removeprefix("call-")) <=
                    self.contract["limits"]["task_max_calls"], "Noncanonical or out-of-budget actor identity")
            if phase == stages["prefix"]:
                require(number in self.selected_worlds and index in (1, 2) and
                        parts[0] == namespace(self.amendment["run_id"], number, "prefix"),
                        "Prefix actor is outside the two fixed eligible worlds")
                kind = "prefix_actors"
            else:
                require(phase == stages["handoff"] and number == self.handoff_world and index in (3, 4) and
                        parts[0] in {namespace(self.amendment["run_id"], number, "recipient/" + arm)
                                     for arm in self.amendment["arms"]}, "Recipient actor is outside the assigned matrix")
                kind = "continuation_actors"
        require(allocation == self.amendment["charges"][kind], "Calibration call charged to an unapproved category")
        return kind

    def child_state(self):
        verify_parent(self.root, self.closure, self.contract)
        requests = journal(self.ledger / "requests.jsonl")[self.closure["before"]["attempts"]:]
        responses = {row["logical_call_id"]: row for row in journal(self.ledger / "responses.jsonl")}
        claims = journal(self.claims_path)
        by_id = {row["logical_call_id"]: row for row in claims}
        require(len(by_id) == len(claims), "A logical calibration attempt was claimed twice")
        counts, tokens, unknown = {}, 0, []
        for row in requests:
            identity = row["logical_call_id"]
            require(identity in by_id, "Global ledger contains an unclaimed post-parent call")
            claim = by_id[identity]
            require(claim["allocation"] == row["allocation"], "Claim/global charge mismatch")
            kind = self.classify(identity, row["allocation"], claim["phase"])
            counts[kind] = counts.get(kind, 0) + 1
            receipt = responses.get(identity, {})
            actual = receipt.get("accounted_actual_tokens")
            tokens += row["reserved_input_output_tokens"] if actual is None else actual
            if actual is None:
                unknown.append(identity)
        return {"attempts": len(requests), "input_output_tokens": tokens, "counts": counts,
                "unknown_spend_requests": unknown, "claimed_logical_ids": list(by_id)}

    def check(self, logical_id=None, allocation=None, reserve=0):
        state = super().check(logical_id, allocation, reserve)
        child = self.child_state()
        limits = self.amendment["child_limits"]
        if child["input_output_tokens"] + reserve > limits["input_output_tokens"]:
            raise BudgetExceeded("Additional calibration token ceiling reached")
        if logical_id is not None:
            kind = self.classify(logical_id, allocation, self.phase)
            if child["attempts"] >= limits["model_attempts"] or child["counts"].get(kind, 0) >= limits[{
                "prefix_actors": "prefix_actor_calls", "memory_builders": "memory_builder_calls",
                "continuation_actors": "continuation_actor_calls"}[kind]]:
                raise BudgetExceeded("Additional calibration phase/attempt ceiling reached")
        return state

    def dispatch(self, payload, logical_id, allocation, *, job=None, deadline=None, prepared=False):
        self.check(logical_id, allocation)
        require(logical_id not in self.child_state()["claimed_logical_ids"], "No retry of a claimed calibration identity")
        append_event(self.claims_path, {"logical_call_id": logical_id, "phase": self.phase, "allocation": allocation})
        return super().dispatch(payload, logical_id, allocation, job=job, deadline=deadline, prepared=prepared)
