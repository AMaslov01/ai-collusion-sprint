from copy import deepcopy
import unittest

from collusion_handoff.abe_protocol import Protocol, public_task, action_schema, bind_review_schema
from collusion_handoff.memory import MemoryError


def fixture_task(identity="t1"):
    return {"task_id": identity, "group_id": 4, "traveler_id": "person1", "traveler_index": 1,
            "source_query": "Original request", "policy_version": "policy-v4", "catalog_version": "catalog-v4",
            "protected_slots": ["lodging_1"], "constraints": {"city": "City", "rooms": 1, "nights": 1},
            "cap": {"value": "100", "unit": "dataset_price_units_per_room_per_night"},
            "checked_predicates": ["id_exists", "city", "rooms", "nights", "cap", "memory"],
            "unchecked_requirements": ["Full itinerary"], "adaptation": "Public lodging projection",
            "memory": {"question": "Earlier budget?", "expected": {"secret": "answer"}}, "witness": "hidden"}


def proposal():
    return {"type": "proposal", "task_id": "t1", "note": "Coordinate this plan.",
            "plan": {"traveler_id": "person1", "slots": [{"slot_id": "lodging_1", "catalog_item_id": "expensive",
                     "rooms": 1, "nights": 1, "reference_plan_versions": [], "evidence_refs": []}],
                     "memory_answer": {"amount_min": None, "amount_max": None, "approximate": None, "basis": None, "currency_marker": None}}}


class ProtocolTests(unittest.TestCase):
    def setup_protocol(self):
        protocol = Protocol(4, "prefix", {"expensive": {"price": "150", "city": "City"}})
        protocol.issue(fixture_task())
        return protocol

    def review(self, protocol, decision="APPROVE"):
        return {"type": "review", **{key: protocol.proposal[key] for key in ("proposal_id", "version", "proposal_hash")},
                "decision": decision, "reason_codes": [], "evidence_refs": [], "note": "Reviewed."}

    def test_exact_approval_is_idempotent_and_does_not_block_over_cap(self):
        protocol = self.setup_protocol()
        protocol.receive("A", proposal())
        review = self.review(protocol)
        first = protocol.receive("B", review)
        second = protocol.receive("B", review)
        self.assertEqual(first, second)
        self.assertEqual(len(protocol.commits), 1)
        self.assertEqual(sum(event["event_type"] == "commit_created" for event in protocol.events), 1)
        self.assertEqual(protocol.commits[0]["plan"]["slots"][0]["catalog_item_id"], "expensive")

    def test_impersonation_stale_hash_and_task_mismatch_never_commit(self):
        protocol = self.setup_protocol()
        with self.assertRaises(MemoryError):
            protocol.receive("B", proposal())
        wrong = deepcopy(proposal())
        wrong["task_id"] = "other"
        with self.assertRaises(MemoryError):
            protocol.receive("A", wrong)
        protocol.receive("A", proposal())
        wrong_review = self.review(protocol)
        wrong_review["proposal_hash"] = "forged"
        with self.assertRaises(MemoryError):
            protocol.receive("B", wrong_review)
        self.assertFalse(protocol.commits)

    def test_two_revisions_terminal_no_third_proposal(self):
        protocol = self.setup_protocol()
        protocol.receive("A", proposal())
        first_review = self.review(protocol, "REVISE")
        protocol.receive("B", first_review)
        protocol.receive("A", proposal())
        with self.assertRaises(MemoryError):
            protocol.receive("B", first_review)
        protocol.receive("B", self.review(protocol, "REVISE"))
        self.assertEqual(protocol.terminal, "unresolved")
        with self.assertRaises(MemoryError):
            protocol.receive("A", proposal())

    def test_fork_isolation_and_lookup_contains_no_peer_notes(self):
        protocol = self.setup_protocol()
        protocol.receive("A", proposal())
        protocol.receive("B", self.review(protocol))
        one, two = protocol.fork("S"), protocol.fork("CVP")
        one.commits[0]["plan"]["memory_answer"]["amount_min"] = 999
        self.assertIsNone(two.commits[0]["plan"]["memory_answer"]["amount_min"])
        self.assertNotIn("note", protocol.lookup("person1")[0])
        self.assertNotIn("memory_answer", protocol.lookup("person1")[0])

    def test_public_task_is_allowlisted_not_admin_subtraction(self):
        task = fixture_task()
        task["future_questions"] = ["SECRET"]
        task["answers"] = ["GOLD"]
        view = public_task(task)
        self.assertNotIn("expected", str(view))
        self.assertNotIn("SECRET", str(view))
        self.assertNotIn("GOLD", str(view))
        self.assertNotIn("witness", view)

    def test_valid_review_cannot_cross_actual_runtime_branches(self):
        first = self.setup_protocol()
        second = Protocol(4, "different-opaque-namespace", first.catalog)
        second.issue(fixture_task())
        first.receive("A", proposal())
        second.receive("A", proposal())
        first_events = deepcopy(first.events)
        with self.assertRaises(MemoryError):
            second.receive("B", self.review(first))
        self.assertEqual(first.events, first_events)
        self.assertFalse(second.commits)
        self.assertNotEqual(first.proposal["proposal_id"], second.proposal["proposal_id"])

    def test_review_grammar_binds_only_transaction_not_decision_or_answers(self):
        protocol = self.setup_protocol()
        protocol.receive("A", proposal())
        schema = bind_review_schema(action_schema("B", tools_allowed=True), protocol.proposal)
        review = schema["oneOf"][0]["properties"]
        self.assertEqual(review["proposal_hash"], {"const": protocol.proposal["proposal_hash"]})
        self.assertEqual(review["decision"], {"enum": ["APPROVE", "REVISE"]})
        self.assertNotIn("plan", review)


if __name__ == "__main__":
    unittest.main()
