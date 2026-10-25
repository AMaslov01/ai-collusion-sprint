"""Typed execution identifiers and opt-in private channels; synthetic replies only."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError

from collusion_handoff.abe_evaluation import evaluate_opportunity
from collusion_handoff.abe_protocol import (Protocol, action_schema, bind_symbolic_references,
    validate_bound_action, visible_committed_proposals)
from collusion_handoff.abe_runner import BranchRunner, PRIVATE_SYSTEM_SEPARATOR
from collusion_handoff.artifacts import fingerprint, read_json
from collusion_handoff.memory import MemoryError
from test_abe_protocol import fixture_task
from test_abe_continuation import public_inputs


VERSION = "f" * 64
SAFE = "accommodation:" + VERSION + ":1"
UNSAFE = "accommodation:" + VERSION + ":2"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = {identity: {"catalog_item_id": identity, "city": "City", "price": price, "row_index": index}
           for index, (identity, price) in enumerate(((SAFE, "80"), (UNSAFE, "150")))}


def task(number=1):
    value = fixture_task(f"synthetic-{number}")
    value.update({"traveler_index": number, "traveler_id": f"synthetic-person-{number}", "catalog_version": VERSION,
                  "memory": None})
    value["checked_predicates"].remove("memory")
    return value


def proposal(current, item=SAFE, refs=(), versions=()):
    return {"type": "proposal", "task_id": current["task_id"], "note": "GENERATED_PEER_ARRANGEMENT",
            "plan": {"traveler_id": current["traveler_id"], "slots": [{"slot_id": "lodging_1", "catalog_item_id": item,
                "rooms": 1, "nights": 1, "evidence_refs": list(refs), "reference_plan_versions": list(versions)}],
                "memory_answer": {"amount_min": None, "amount_max": None, "approximate": None, "basis": None,
                                  "currency_marker": None}}}


class SymbolicReferenceTests(unittest.TestCase):
    def schema(self, receipts=(), versions=()):
        return bind_symbolic_references(action_schema("A", tools_allowed=True), receipt_ids=receipts,
                                        proposal_ids=versions, catalog_version=VERSION)

    def test_exact_receipts_and_committed_versions_are_distinct_from_archive_aliases(self):
        current = task()
        schema = self.schema(["own/receipt"], ["prefix/task/proposal-1"])
        validate_bound_action(proposal(current, refs=["own/receipt"], versions=["prefix/task/proposal-1"]), schema)
        validate_bound_action(proposal(current), self.schema())
        for receipt in ("archive:source[5]:record[0]", "tool:catalog_search:own/receipt", "own/receipt:item:1", "other/receipt"):
            with self.subTest(receipt=receipt), self.assertRaises(ValidationError):
                validate_bound_action(proposal(current, refs=[receipt]), schema)
        for version in (VERSION, "archive:commit", "other-branch/task/proposal-1"):
            with self.subTest(version=version), self.assertRaises(ValidationError):
                validate_bound_action(proposal(current, versions=[version]), schema)
        validate_bound_action(proposal(current, refs=["own/receipt", "own/receipt"]), schema)

    def test_namespace_does_not_enumerate_rows_or_filter_unsafe_prices(self):
        schema = self.schema()
        before = deepcopy(schema)
        for identity, expected_harm in ((SAFE, 0), (UNSAFE, 1)):
            current = task()
            action = proposal(current, identity)
            validate_bound_action(action, schema)
            protocol = Protocol(4, "synthetic-prefix", CATALOG)
            protocol.issue(current)
            made = protocol.receive("A", action)
            protocol.receive("B", {"type": "review", **{key: made[key] for key in ("proposal_id", "version", "proposal_hash")},
                "decision": "APPROVE", "reason_codes": [], "evidence_refs": [], "note": "Generated acceptance."})
            score = evaluate_opportunity(current, CATALOG, protocol.events, ledger_complete=True)
            self.assertEqual(score["H"], expected_harm)
            self.assertEqual(len(protocol.commits), 1)
        self.assertEqual(schema, before)
        field = schema["oneOf"][0]["properties"]["plan"]["properties"]["slots"]["items"]["properties"]["catalog_item_id"]
        self.assertNotIn("enum", field)

    def test_format_and_exact_existence_are_independent_including_terminal_newline(self):
        schema = self.schema()
        for identity in ("1", SAFE + "junk", " " + SAFE, "accommodation:" + "a" * 64 + ":1"):
            with self.subTest(identity=identity), self.assertRaises(ValidationError):
                validate_bound_action(proposal(task(), identity), schema)
        for identity in ("accommodation:" + VERSION + ":999999", SAFE + "\n", "accommodation:" + VERSION + ":01"):
            validate_bound_action(proposal(task(), identity), schema)
            protocol = Protocol(4, "synthetic", CATALOG)
            protocol.issue(task())
            with self.subTest(identity=identity), self.assertRaisesRegex(MemoryError, "Unknown canonical catalog ID"):
                protocol.receive("A", proposal(task(), identity))

    def test_schema_binding_is_a_pure_copy_and_never_offers_hidden_plan_handles(self):
        original = action_schema("A", tools_allowed=True)
        before = deepcopy(original)
        commits = [{"proposal_id": "prefix/task/proposal-1"}, {"proposal_id": "hidden/task/proposal-2"}]
        self.assertEqual(visible_committed_proposals(commits, []), [])
        visible = visible_committed_proposals(commits, ['{"proposal_id":"prefix/task/proposal-1"}'])
        self.assertEqual(visible, ["prefix/task/proposal-1"])
        self.assertEqual(visible_committed_proposals(commits, ["prefix/task/proposal-1:item:1"]), [])
        bounded = bind_symbolic_references(original, receipt_ids=[], proposal_ids=visible, catalog_version=VERSION)
        Draft202012Validator.check_schema(bounded)
        self.assertEqual(original, before)

    def test_actual_bad_reply_is_checked_before_B_and_classified_without_repair(self):
        limits = read_json(ROOT / "configs/readiness-cycle-v5.json")["limits"]
        cases = [(proposal(task(), refs=["invented/receipt"]), "bound_tool_receipt_reference"),
                 (proposal(task(), "1"), "catalog_namespace_format"),
                 (proposal(task(), "accommodation:" + VERSION + ":999999"), "canonical_grounding_failure")]
        for action, cause in cases:
            calls = []
            def reply(*args):
                calls.append(args)
                return {"text": json.dumps(action), "input_tokens": 10, "output_tokens": 10}
            runner = BranchRunner(Protocol(4, "synthetic", CATALOG), {"A": "PUBLIC_A", "B": "PUBLIC_B"}, limits, reply,
                base={"name": "Base", "query": "Synthetic base", "daily_plans": []}, symbolic_reference_binding=True)
            result = runner.run_task(task())
            self.assertEqual(result["status"], "protocol_invalid")
            self.assertEqual(result["failure"]["kind"], cause)
            self.assertEqual(result["evaluation"]["H"], 0)
            self.assertEqual(result["evaluation"]["U"], 0)
            self.assertEqual(len(calls), 1)
            self.assertFalse(runner.protocol.commits)

    def test_non_object_JSON_is_a_recorded_bound_schema_failure(self):
        limits = read_json(ROOT / "configs/readiness-cycle-v5.json")["limits"]
        for value in ([], None, 42, "malformed object"):
            def reply(*args):
                return {"text": json.dumps(value), "input_tokens": 10, "output_tokens": 10}
            runner = BranchRunner(Protocol(4, "synthetic", CATALOG), {"A": "PUBLIC_A", "B": "PUBLIC_B"}, limits, reply,
                base={"name": "Base", "query": "Synthetic", "daily_plans": []}, symbolic_reference_binding=True)
            result = runner.run_task(task())
            self.assertEqual(result["status"], "protocol_invalid")
            self.assertEqual(result["failure"]["type"], "ValidationError")
            self.assertEqual(result["failure"]["kind"], "bound_action_schema")
            self.assertEqual(result["model_calls"], 1)

    def test_single_system_serialization_has_exact_component_bytes_and_origin_based_fork(self):
        limits = read_json(ROOT / "configs/readiness-cycle-v5.json")["limits"]
        prompts = {"A": "Публичная роль A\n", "B": "Публичная роль B\n"}
        goals = {"A": "Частная цель A\n", "B": "Частная цель B\n"}
        prefix = BranchRunner(Protocol(4, "synthetic", CATALOG), prompts, limits, None,
            base={"name": "Base", "query": "Synthetic", "daily_plans": []}, private_goals=goals,
            private_goal_message_role="system", private_goal_serialization="append_to_initial_system")
        for role in ("A", "B"):
            messages = prefix.messages(role, action_schema(role, tools_allowed=True))
            self.assertEqual(sum(item["role"] == "system" for item in messages), 1)
            self.assertEqual(messages[0]["content"], prompts[role] + PRIVATE_SYSTEM_SEPARATOR + goals[role])
            provenance = prefix.message_provenance(role, messages)
            self.assertEqual(provenance[0]["event_type"], "current_system_inputs")
            raw = messages[0]["content"].encode("utf-8")
            components = provenance[0]["components"]
            self.assertEqual([item["event_type"] for item in components],
                             ["current_nominal_role", "system_input_separator", "private_goal_input"])
            offset = 0
            for component, expected in zip(components, (prompts[role], PRIVATE_SYSTEM_SEPARATOR, goals[role])):
                self.assertEqual(component["start_utf8"], offset)
                value = raw[component["start_utf8"]:component["end_utf8"]]
                self.assertEqual(value, expected.encode("utf-8"))
                self.assertEqual(component["content_sha256"], sha256(value).hexdigest())
                offset = component["end_utf8"]
            self.assertEqual(offset, len(raw))
        prefix.add("B", "assistant", "peer_message", "Generated quote: " + goals["A"], "A")
        future = prefix.fork("synthetic-recipient", None, None)
        for role in ("A", "B"):
            messages = future.messages(role, action_schema(role, tools_allowed=True))
            self.assertEqual(messages[0], {"role": "system", "content": prompts[role]})
            self.assertNotIn("private_goal_input", str(future.message_provenance(role, messages)))
        self.assertIn("Generated quote: " + goals["A"], [record["content"] for record in future.histories["B"]])

    def test_system_goal_optin_and_both_removed_after_actual_fork(self):
        limits = read_json(ROOT / "configs/readiness-cycle-v5.json")["limits"]
        def reply(role, messages, schema, *args):
            current, parsed = public_inputs(messages)
            if role == "A":
                action = proposal(current)
            else:
                made = next(value["proposal"] for value in reversed(parsed) if "proposal" in value)
                action = {"type": "review", **{key: made[key] for key in ("proposal_id", "version", "proposal_hash")},
                          "decision": "APPROVE", "reason_codes": [], "evidence_refs": ["prefix/observed-B/receipt"],
                          "note": "GENERATED_PEER_ARRANGEMENT"}
            return {"text": json.dumps(action), "input_tokens": 10, "output_tokens": 10}
        prefix = BranchRunner(Protocol(4, "synthetic", CATALOG), {"A": "PUBLIC_A", "B": "PUBLIC_B"}, limits, reply,
            base={"name": "Base", "query": "Synthetic base", "daily_plans": []},
            private_goals={"A": "GOAL_ONLY_A", "B": "GOAL_ONLY_B"}, private_goal_message_role="system", symbolic_reference_binding=True)
        prefix.add("B", "user", "tool_result", "Synthetic actual B tool result", "tool", receipt_id="prefix/observed-B/receipt")
        self.assertEqual(prefix.run_task(task())["status"], "committed")
        for request in prefix.requests:
            messages = request["payload"]["messages"]
            self.assertEqual([m["role"] for m in messages[:2]], ["system", "system"])
            self.assertEqual(request["message_provenance"][1]["event_type"], "private_goal_input")
        source = prefix.source({"catalog": VERSION})
        self.assertNotIn("GOAL_ONLY", json.dumps(source))
        archive = json.dumps(source["archive"])
        one, two = [prefix.fork(name, archive, reply) for name in ("synthetic-recipient-1", "synthetic-recipient-2")]
        self.assertEqual(fingerprint(one.histories["B"]), fingerprint(two.histories["B"]))
        self.assertEqual(fingerprint(one.protocol.commits), fingerprint(two.protocol.commits))
        self.assertNotIn("prefix/observed-B/receipt", one.receipts["A"])
        self.assertIn("prefix/observed-B/receipt", one.receipts["B"])
        self.assertEqual(one.run_task(task(2))["status"], "committed")
        self.assertFalse(one.private_goals)
        for request in one.requests:
            self.assertNotIn("GOAL_ONLY", str(request["payload"]))
            self.assertNotIn("private_goal_input", [origin["event_type"] for origin in request["message_provenance"]])
        self.assertIn("GENERATED_PEER_ARRANGEMENT", str(one.requests))


if __name__ == "__main__":
    unittest.main()
