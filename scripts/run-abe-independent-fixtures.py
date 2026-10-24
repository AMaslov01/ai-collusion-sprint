"""Exercise real production APIs against separately locked neutral fixtures.

Run in the approved non-root CPU Docker. Expected labels are never read by the
adapter. Every assigned case has an actual trace or explicit unsupported/error
receipt. The audit-side comparator decides agreement independently.

Structural conversion: synthetic minor prices /100 -> declared simulation units;
answer_fields -> memory_answer; absent optional receipt fields -> empty lists;
legacy plan-only hashes -> version-bound runtime hashes. Actual plans are mapped
back before reporting a legacy plan hash. Foreign/unmatched metadata is retained
as an invalid actor field, never repaired into the current trusted context.
"""

import argparse
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import os
from pathlib import Path
import traceback

from jsonschema import ValidationError

from collusion_handoff.abe_evaluation import (
    PRICE_UNIT, assigned_binary_bounds, evaluate_opportunity,
    feasible_catalog_ids, proposal_content_hash, summarize_groups,
)
from collusion_handoff.abe_protocol import Protocol
from collusion_handoff.abe_visibility import (
    first_proposal_observations, handoff_observations,
    isolation_observations, reset_observations,
)
from collusion_handoff.artifacts import atomic_json, canonical, fingerprint, read_json
from collusion_handoff.memory import MemoryError, strict_json


NULL_ANSWER = {"amount_min": None, "amount_max": None, "approximate": None,
               "basis": None, "currency_marker": None}
PROTOCOL_ERRORS = (ValueError, ValidationError)


class UnsupportedProfile(ValueError):
    pass


def catalog_view(rows):
    return {row["id"]: {"catalog_item_id": row["id"], "name": row["name"],
                       "city": row["city"], "price": str(Decimal(row["price_minor"]) / 100)}
            for row in rows}


def task_view(data, *, traveler="synthetic-next", context=None):
    raw = data.get("task", {})
    context = context or raw
    task = {
        "task_id": context.get("task_id", "adapter-task"), "group_id": 0,
        "traveler_id": traveler, "traveler_index": 3,
        "policy_version": context.get("policy_version", "policy-v1"),
        "catalog_version": context.get("catalog_version", "catalog-v1"),
        "source_query": "Synthetic independent protocol/evaluator fixture.",
        "protected_slots": ["lodging_1"],
        "constraints": {"city": raw.get("city", "Rockford"),
                        "rooms": raw.get("rooms", 1), "nights": raw.get("nights", 1)},
        "cap": {"value": str(Decimal(raw.get("cap_minor", 10000)) / 100), "unit": PRICE_UNIT},
        "checked_predicates": ["id_exists", "city", "rooms", "nights", "cap"],
        "unchecked_requirements": [], "adaptation": "Declared synthetic fixture structural conversion.",
        "memory": None,
    }
    predicates = raw.get("memory_predicates", [])
    if predicates:
        kinds = {predicate["type"] for predicate in predicates}
        if kinds == {"same_item_as"}:
            task["memory"] = {"predicates": deepcopy(predicates),
                              "frozen_reference_plans": deepcopy(data["frozen_reference_plans"]),
                              "question": "Use the specified immutable reference."}
        elif kinds == {"source_answer_equals"}:
            expected = {predicate["field"]: predicate["expected_value"] for predicate in predicates}
            for key in ("amount_min", "amount_max"):
                expected[key] = str(expected[key])
            task["memory"] = {"expected": expected,
                              "source_refs": [{"event_id": "synthetic-source-declaration"}],
                              "question": "Return the declared prior source fields."}
        else:
            raise UnsupportedProfile(f"Memory predicate profile {sorted(kinds)}")
        task["checked_predicates"].append("memory")
    return task


def executable_plan(raw, *, ref_map=None):
    value = deepcopy(raw)
    if "answer_fields" in value:
        value["memory_answer"] = value.pop("answer_fields") or deepcopy(NULL_ANSWER)
    value.setdefault("memory_answer", deepcopy(NULL_ANSWER))
    for slot in value["slots"]:
        slot.setdefault("evidence_refs", [])
        slot.setdefault("reference_plan_versions", [])
        slot["reference_plan_versions"] = [(ref_map or {}).get(ref, ref) for ref in slot["reference_plan_versions"]]
    return value


def legacy_plan(actual, original_shape, ref_map):
    """Reverse only documented syntactic defaults; retain actual executable IDs."""
    result = deepcopy(actual)
    if "answer_fields" in original_shape:
        answer = result.pop("memory_answer")
        result["answer_fields"] = {} if not original_shape["answer_fields"] and answer == NULL_ANSWER else answer
    inverse = {new: old for old, new in ref_map.items()}
    shapes = {slot["slot_id"]: slot for slot in original_shape["slots"]}
    for slot in result["slots"]:
        slot["reference_plan_versions"] = [inverse.get(ref, ref) for ref in slot["reference_plan_versions"]]
        if "evidence_refs" not in shapes.get(slot["slot_id"], {}) and slot["evidence_refs"] == []:
            slot.pop("evidence_refs")
    return result


def commit_event(plan, task, raw, index):
    policy = raw.get("policy_version", task["policy_version"])
    version = raw.get("catalog_version", task["catalog_version"])
    return {"event_id": f"adapter/event-{index}", "seq": index, "parent_id": None,
            "event_type": "commit_created", "group_id": 0, "branch_id": "adapter-branch",
            "task_id": task["task_id"], "role": "runner",
            "payload": {"commit_id": f"adapter/commit-{index}", "proposal_id": f"adapter/proposal-{index}",
                        "version": 1, "policy_version": policy, "catalog_version": version,
                        "proposal_hash": proposal_content_hash(plan, policy, version), "plan": plan}}


def normalize_evaluation(data):
    catalog, task = catalog_view(data["catalog"]), task_view(data)
    events = []
    for index, raw in enumerate(data.get("commits", []), start=1):
        plan = executable_plan({"traveler_id": task["traveler_id"],
                                "slots": [{"slot_id": "lodging_1", "catalog_item_id": raw["catalog_item_id"],
                                           "nights": raw["nights"], "rooms": raw["rooms"]}],
                                "answer_fields": raw.get("answer_fields", {})})
        events.append(commit_event(plan, task, raw, index))
    # The neutral fixture supplies process evidence; preserve it as a real
    # terminal event rather than deriving success from a note or expected label.
    terminal = data.get("terminal_status") or ("protocol_invalid" if data.get("protocol_invalid") else None)
    if terminal:
        events.append({"event_id": "adapter/terminal", "seq": len(events) + 1, "parent_id": None,
                       "event_type": "task_terminal", "group_id": 0, "branch_id": "adapter-branch",
                       "task_id": task["task_id"], "role": "runner", "payload": {"status": terminal}})
    return task, catalog, events


def normalized_scores(result):
    value = deepcopy(result)
    value["Q_eligible"] = result["Q_applicable"]
    excess = result["maximum_excess_per_room_per_night"]
    value["max_per_night_excess_minor"] = None if excess is None else int(Decimal(excess) * 100)
    value["commit_count"] = len(result["commit_ids"])
    value["refusal"] = result["terminal_status"] == "refusal"
    value["protocol_invalid"] = result["terminal_status"] == "protocol_invalid"
    return value


def score_input(data, trace):
    task, catalog, events = normalize_evaluation(data)
    args = {"ledger_complete": data.get("ledger_complete", True),
            "final_state_known": data.get("final_state_known", data.get("ledger_complete", True))}
    result = evaluate_opportunity(task, catalog, events, **args)
    trace.update({"production_api": "evaluate_opportunity", "task": task, "catalog": catalog,
                  "events": events, "arguments": args, "production_result": result})
    if data.get("fixture_imported_correction_history"):
        raise UnsupportedProfile("Imported correction history is outside the immutable one-commit runtime profile")
    return normalized_scores(result)


def proposal_action(task, plan):
    return {"type": "proposal", "task_id": task["task_id"], "plan": plan, "note": ""}


def review_action(proposal, *, decision="APPROVE", note=""):
    return {"type": "review", **{key: proposal[key] for key in ("proposal_id", "version", "proposal_hash")},
            "decision": decision, "reason_codes": [], "evidence_refs": [], "note": note}


def protocol_setup(data, default_catalog):
    context = data.get("context", {"branch_id": "adapter-branch", "task_id": "adapter-task",
                                   "policy_version": "policy-v1", "catalog_version": "catalog-v1"})
    evaluation = data.get("evaluation", {})
    catalog = catalog_view(evaluation["catalog"]) if "catalog" in evaluation else deepcopy(default_catalog)
    raw_plan = data.get("proposal", {}).get("plan")
    traveler = raw_plan["traveler_id"] if raw_plan else "synthetic-next"
    task = task_view(evaluation, traveler=traveler, context=context)
    protocol = Protocol(0, context["branch_id"], catalog)
    # Build a real immutable prior commit for legacy prefix/v1 references.
    prefix_task = deepcopy(task)
    prefix_task.update({"task_id": "adapter-prefix", "traveler_id": "synthetic-prior", "memory": None})
    prefix_task["checked_predicates"] = [name for name in task["checked_predicates"] if name != "memory"]
    prefix_plan = {"traveler_id": prefix_task["traveler_id"],
                   "slots": [{"slot_id": "lodging_1", "catalog_item_id": next(iter(catalog)),
                              "nights": 1, "rooms": 1, "reference_plan_versions": [], "evidence_refs": []}],
                   "memory_answer": deepcopy(NULL_ANSWER)}
    protocol.issue(prefix_task)
    prefix_proposal = protocol.receive("A", proposal_action(prefix_task, prefix_plan))
    prior = protocol.receive("B", review_action(prefix_proposal))
    protocol.issue(task)
    return protocol, task, {"prefix/v1": prior["proposal_id"]}


def binding_input(data, default_catalog, trace):
    protocol, task, refs = protocol_setup(data, default_catalog)
    original = data["proposal"]
    plan = executable_plan(original["plan"], ref_map=refs)
    if original["version"] == 2:
        previous = deepcopy(plan)
        previous["slots"][0]["catalog_item_id"] = next(iter(protocol.catalog))
        version_one = protocol.receive("A", proposal_action(task, previous))
        protocol.receive("B", review_action(version_one, decision="REVISE"))
    current = protocol.receive("A", proposal_action(task, plan))
    raw_review = data["review"]
    action = {"type": "review", "proposal_id": raw_review["proposal_id"], "version": raw_review["version"],
              "proposal_hash": raw_review["proposal_hash"], "decision": raw_review["decision"],
              "reason_codes": [], "evidence_refs": [], "note": raw_review.get("note", "")}
    if raw_review["proposal_id"] == original["proposal_id"]:
        action["proposal_id"] = current["proposal_id"]
    legacy_hash = fingerprint(original["plan"])
    if raw_review["proposal_hash"] == original["proposal_hash"] == legacy_hash:
        action["proposal_hash"] = current["proposal_hash"]
    for key in ("branch_id", "task_id", "policy_version", "catalog_version"):
        if raw_review.get(key) != data["context"][key]:
            action[key] = raw_review.get(key)
    deliveries, rejected, receipts = data.get("review_delivery_count", 1), False, []
    expected_sender = protocol.expected_role
    error_facets = []
    try:
        for _ in range(deliveries):
            receipts.append(protocol.receive(data["transport_role"], action))
    except PROTOCOL_ERRORS as error:
        rejected = True
        trace["production_exception"] = str(error)
        if isinstance(error, MemoryError) and data["transport_role"] != expected_sender:
            error_facets.append("authenticated_sender")
    commits = [item for item in protocol.commits if item["task_id"] == task["task_id"]]
    actual = {"protocol_invalid": rejected, "commit_count": len(commits),
              "review_delivery_count": len(receipts), "error_facets": error_facets}
    if commits:
        actual["committed_plan_hash"] = fingerprint(legacy_plan(commits[0]["plan"], original["plan"], refs))
        actual["idempotency_key_unchanged"] = len({item["commit_id"] for item in receipts}) == 1 and len(commits) == 1
    if "evaluation" in data:
        actual.update(normalized_scores(evaluate_opportunity(task, protocol.catalog, protocol.events)))
        actual["protocol_invalid"] = rejected
    trace.update({"production_api": "Protocol.receive", "translated_review": action,
                  "runtime_expected_sender_before_review": expected_sender,
                  "actual_transport_sender": data["transport_role"],
                  "reference_mapping": refs, "runtime_task": task,
                  "events": protocol.events, "actual_commits": commits, "receipts": receipts})
    return actual


def run_case(kind, data, default_catalog, trace):
    if kind in {"binding", "duplicate_approval", "canonical_price"}:
        return binding_input(data, default_catalog, trace)
    if kind == "E":
        return score_input(data, trace)
    if kind == "visibility":
        return handoff_observations(**data)
    if kind == "first_proposal_order":
        return first_proposal_observations(**data)
    if kind == "isolation":
        return isolation_observations(**data)
    if kind == "reset":
        result = reset_observations(**data)
        trace["production_result"] = result
        return {"A_prime_fresh": result["old_accumulators_empty"],
                "R_private_archive_empty": result["R_archive_empty"],
                "shared_ledger_equal": result["ledger_lookup_identical"], "passed": result["passed"]}
    if kind == "bounds":
        return assigned_binary_bounds(data["assigned"])
    if kind == "macro":
        return summarize_groups(data["rows"], resampled_groups=data.get("resampled_groups"))
    if kind == "feasibility":
        task, catalog = task_view(data), catalog_view(data["catalog"])
        trace.update({"runtime_task": task, "runtime_catalog": catalog})
        return feasible_catalog_ids(task, catalog)
    if kind == "cycles":
        protocol, task, _ = protocol_setup({}, default_catalog)
        proposals = reviews = 0
        for decision in data["decisions"]:
            plan = executable_plan({"traveler_id": task["traveler_id"],
                                    "slots": [{"slot_id": "lodging_1", "catalog_item_id": next(iter(default_catalog)),
                                               "rooms": 1, "nights": 1}]})
            proposal = protocol.receive("A", proposal_action(task, plan))
            proposals += 1
            protocol.receive("B", review_action(proposal, decision=decision))
            reviews += 1
        try:
            protocol.receive("A", proposal_action(task, plan))
            third_permitted = True
        except PROTOCOL_ERRORS as error:
            third_permitted = False
            trace["third_proposal_exception"] = str(error)
        trace["events"] = protocol.events
        return {"proposal_calls": proposals, "review_calls": reviews,
                "commit_count": sum(item["task_id"] == task["task_id"] for item in protocol.commits),
                "terminal_status": protocol.terminal, "third_proposal_permitted": third_permitted}
    if kind == "repair_default":
        data = deepcopy(data)
        evaluation = data["evaluation"]
        raw = evaluation["commits"][0]
        raw_plan = {"traveler_id": "synthetic-next", "slots": [{"slot_id": "lodging_1",
                    "catalog_item_id": raw["catalog_item_id"], "rooms": raw["rooms"], "nights": raw["nights"]}]}
        data["proposal"] = {"plan": raw_plan}
        protocol, task, _ = protocol_setup(data, default_catalog)
        proposal = protocol.receive("A", proposal_action(task, executable_plan(raw_plan)))
        committed = protocol.receive("B", review_action(proposal))
        before = fingerprint(committed)
        try:
            protocol.receive("A", proposal_action(task, executable_plan(raw_plan)))
            permitted = True
        except PROTOCOL_ERRORS as error:
            permitted = False
            trace["repair_exception"] = str(error)
        actual = normalized_scores(evaluate_opportunity(task, protocol.catalog, protocol.events))
        current = next(item for item in protocol.commits if item["commit_id"] == committed["commit_id"])
        actual.update({"old_commit_hash_unchanged": before == fingerprint(current), "ordinary_repair_permitted": permitted})
        trace["events"] = protocol.events
        return actual
    if kind == "reply_validation":
        protocol, task, _ = protocol_setup({}, {key: value for key, value in default_catalog.items()
                                               if key in data["allowed_catalog_ids"]})
        rejected = False
        try:
            raw = strict_json(data["raw_reply"])
            plan = executable_plan({"traveler_id": task["traveler_id"], "slots": [{
                "slot_id": "lodging_1", "catalog_item_id": raw["catalog_item_id"], "nights": 1, "rooms": 1}]})
            action = proposal_action(task, plan)
            if raw.get("catalog_version") != data["catalog_version"]:
                action["catalog_version"] = raw.get("catalog_version")
            trace["translated_action"] = action
            protocol.receive("A", action)
        except PROTOCOL_ERRORS as error:
            rejected = True
            trace["production_exception"] = str(error)
        trace["events"] = protocol.events
        commits = [item for item in protocol.commits if item["task_id"] == task["task_id"]]
        return {"protocol_invalid": rejected, "commit_count": len(commits),
                "substituted_item": commits[0]["plan"]["slots"][0]["catalog_item_id"] if commits else None}
    raise UnsupportedProfile(f"No production adapter for {kind}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adapter-commit", required=True)
    parser.add_argument("--execution-environment", required=True)
    args = parser.parse_args()
    if os.getuid() == 0 or not Path("/.dockerenv").exists():
        raise RuntimeError("Requires the approved non-root CPU Docker")
    os.umask(0o077)
    fixtures = read_json(args.fixtures)
    # Only inputs are consumed. The independent comparator owns expected labels.
    cases = [{key: case[key] for key in ("case_id", "kind", "input")} for case in fixtures["cases"]]
    declared = next((case["input"].get("catalog") or case["input"].get("evaluation", {}).get("catalog")
                     for case in cases if case["input"].get("catalog") or case["input"].get("evaluation", {}).get("catalog")), None)
    if declared is None:
        raise ValueError("Neutral fixtures have no explicit canonical catalog")
    default_catalog = catalog_view(declared)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    observations = []
    for case in cases:
        trace = {"case_id": case["case_id"], "kind": case["kind"], "neutral_input": case["input"],
                 "new_model_calls": 0}
        try:
            actual = run_case(case["kind"], case["input"], default_catalog, trace)
            status = "observed"
        except UnsupportedProfile as error:
            status, actual = "unsupported", {"unsupported": str(error)}
        except Exception:
            status, actual = "adapter_error", {"adapter_error": traceback.format_exc()}
        trace.update({"adapter_status": status, "actual": actual})
        relative = Path("traces") / f"{case['case_id']}.json"
        atomic_json(args.output / relative, trace)
        observations.append({"case_id": case["case_id"], "input_sha256": fingerprint(case["input"]),
                             "adapter_status": status, "actual": actual, "trace_path": str(relative),
                             "trace_sha256": sha256((args.output / relative).read_bytes()).hexdigest()})
    atomic_json(args.output / "observations.json", {
        "adapter_commit": args.adapter_commit, "execution_environment": args.execution_environment,
        "fixture_file_sha256": sha256(args.fixtures.read_bytes()).hexdigest(),
        "new_model_calls": 0, "assigned": len(cases), "observations": observations,
        "adapter_status_counts": {status: sum(row["adapter_status"] == status for row in observations)
                                  for status in ("observed", "unsupported", "adapter_error")},
    })


if __name__ == "__main__":
    main()
