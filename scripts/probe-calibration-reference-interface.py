"""Production schema observations for independently locked CPU-only cases.

Expected fields are not consulted. The independent reviewer compares this output to
the locked labels. No model client or native world is used.
"""

import argparse
from hashlib import sha256
import os
from pathlib import Path

from jsonschema import ValidationError

from collusion_handoff.abe_protocol import (Protocol, action_schema, bind_symbolic_references,
                                           validate_bound_action, visible_committed_proposals)
from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.memory import MemoryError


def current_task(version):
    return {"task_id": "synthetic-reference-task", "group_id": 0, "traveler_id": "synthetic-person", "traveler_index": 1,
            "source_query": "Synthetic CPU-only case", "policy_version": "synthetic-reference-policy", "catalog_version": version,
            "protected_slots": ["lodging_1"], "constraints": {"city": "Synthetic City", "rooms": 1, "nights": 1},
            "cap": {"value": "100", "unit": "dataset_price_units_per_room_per_night"},
            "checked_predicates": ["id_exists", "city", "rooms", "nights", "cap"], "unchecked_requirements": [],
            "adaptation": "Synthetic identifier case, no scientific task or booking", "memory": None}


def proposal(identity, receipts=(), versions=()):
    return {"type": "proposal", "task_id": "synthetic-reference-task", "note": "Synthetic reference test.",
            "plan": {"traveler_id": "synthetic-person", "slots": [{"slot_id": "lodging_1", "catalog_item_id": identity,
                     "rooms": 1, "nights": 1, "evidence_refs": list(receipts), "reference_plan_versions": list(versions)}],
                     "memory_answer": {"amount_min": None, "amount_max": None, "approximate": None, "basis": None, "currency_marker": None}}}


def admitted(action, schema):
    try:
        validate_bound_action(action, schema)
        return True
    except (MemoryError, ValidationError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--namespace", type=Path, required=True)
    parser.add_argument("--visibility", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.getuid() == 0 or not Path("/.dockerenv").exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise RuntimeError("Requires non-root CPU Docker without assigned GPU")
    if args.output.exists():
        raise RuntimeError("Do not replace an independent observation")
    os.umask(0o077)
    results = {"references": [], "namespace": [], "visibility": []}
    for case in read_json(args.references)["cases"]:
        value, version = case["input"], "f" * 64
        role = value["role"]
        bounded = bind_symbolic_references(action_schema(role, tools_allowed=True), receipt_ids=value["role_observed_receipts"],
                                           proposal_ids=value["known_committed_proposal_ids"], catalog_version=version)
        action = proposal("accommodation:" + version + ":1", value["evidence_refs"], value["reference_plan_versions"]) if role == "A" else {
            "type": "review", "proposal_id": "synthetic/current/proposal-1", "version": 1, "proposal_hash": "a" * 64,
            "decision": "APPROVE", "reason_codes": [], "evidence_refs": value["evidence_refs"], "note": "Synthetic CPU-only review."}
        results["references"].append({"case_id": case["case_id"], "typed_reference_fields_allowed": admitted(action, bounded)})
    for case in read_json(args.namespace)["cases"]:
        value = case["input"]
        version, identity = value["current_public_catalog_version"], value["candidate_catalog_item_id"]
        bounded = bind_symbolic_references(action_schema("A", tools_allowed=True), receipt_ids=[], proposal_ids=[], catalog_version=version)
        action = proposal(identity)
        protocol = Protocol(0, "synthetic-namespace", {name: {} for name in value["pinned_canonical_catalog_ids_for_membership_check"]})
        protocol.issue(current_task(version))
        try:
            protocol.receive("A", action)
            exists = True
        except (MemoryError, ValidationError):
            exists = False
        tool = {"type": "tool_request", "tool": "catalog_get", "arguments": {"catalog_item_id": identity}}
        tool_admission = {role: admitted(tool, bind_symbolic_references(action_schema(role, tools_allowed=True),
            receipt_ids=[], proposal_ids=[], catalog_version=version)) for role in ("A", "B")}
        results["namespace"].append({"case_id": case["case_id"], "namespace_pattern_admitted": admitted(action, bounded),
                                     "catalog_get_namespace_admitted": tool_admission, "exact_canonical_membership": exists})
    for case in read_json(args.visibility)["cases"]:
        value = case["input"]
        actual = visible_committed_proposals([{"proposal_id": identity} for identity in value["actual_committed_proposal_ids"]],
                                             value["actual_visible_history_text"])
        results["visibility"].append({"case_id": case["case_id"], "visible_committed_proposal_ids": actual})
    atomic_json(args.output, {"schema": "calibration_reference_observations_v1", "new_model_calls": 0,
                "expected_labels_consulted": False, "observations": results,
                "input_file_sha256": {str(path): sha256(path.read_bytes()).hexdigest() for path in
                                      (args.references, args.namespace, args.visibility)}})


if __name__ == "__main__":
    main()
