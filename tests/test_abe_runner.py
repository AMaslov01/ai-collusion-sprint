from copy import deepcopy
import json
from pathlib import Path
import unittest

from collusion_handoff.abe_protocol import Protocol
from collusion_handoff.abe_runner import BranchRunner, ServiceFailure
from collusion_handoff.artifacts import fingerprint, read_json
from test_abe_protocol import fixture_task


class RecipientTests(unittest.TestCase):
    def settings(self):
        return read_json(Path(__file__).resolve().parents[1] / "configs/readiness-cycle-v4.json")["limits"]

    def fake_generator(self, role, messages, schema, logical_id, deadline, remaining_tokens):
        self.wires.append({"role": role, "messages": deepcopy(messages)})
        user = []
        for item in messages:
            try:
                user.append(json.loads(item["content"]))
            except json.JSONDecodeError:
                pass
        task = next(item["current_public_task"] for item in reversed(user) if "current_public_task" in item)
        if role == "A":
            action = {"type": "proposal", "task_id": task["task_id"], "note": "Keep public requirements.",
                      "plan": {"traveler_id": task["traveler_id"], "slots": [{"slot_id": "lodging_1", "catalog_item_id": "safe",
                               "rooms": 1, "nights": 1, "reference_plan_versions": [], "evidence_refs": []}],
                               "memory_answer": {"amount_min": None, "amount_max": None, "approximate": None, "basis": None, "currency_marker": None}}}
        else:
            proposal = next(item["proposal"] for item in reversed(user) if "proposal" in item)
            action = {"type": "review", **{key: proposal[key] for key in ("proposal_id", "version", "proposal_hash")},
                      "decision": "APPROVE", "reason_codes": [], "evidence_refs": [], "note": "Reviewed exact version."}
        return {"text": json.dumps(action), "input_tokens": 20, "output_tokens": 20}

    def task(self, number):
        task = fixture_task(f"task-{number}")
        task.update({"traveler_id": f"person-{number}", "traveler_index": number, "memory": None})
        task["checked_predicates"].remove("memory")
        task["answers"] = "HIDDEN_GOLD"
        task["future_questions"] = "FUTURE_SECRET"
        return task

    def test_real_recipient_path_has_fresh_A_identical_B_and_no_seed_bypass(self):
        self.wires = []
        catalog = {"safe": {"catalog_item_id": "safe", "source_sha256": "catalog-v4", "row_index": 0, "price": "80", "city": "City"}}
        prefix = BranchRunner(Protocol(4, "session-prefix", catalog), {"A": "Planner", "B": "Reviewer"}, self.settings(), self.fake_generator,
                              base={"name": "Base", "query": "Public base query", "daily_plans": []},
                              private_goals={"A": "PRIVATE_SEED_A", "B": "PRIVATE_SEED_B"})
        for number in (1, 2):
            result = prefix.run_task(self.task(number))
            self.assertEqual(result["status"], "committed")
            self.assertEqual(result["evaluation"]["H"], 0)
        source = prefix.source({"catalog": "catalog-v4"})
        self.assertNotIn("PRIVATE_SEED", json.dumps(source))
        branches = [prefix.fork(f"session-{index}", "Historical summary" if index else "", self.fake_generator) for index in range(6)]
        self.assertEqual(len({fingerprint(branch.histories["B"]) for branch in branches}), 1)
        self.assertTrue(all(len(branch.histories["A"]) == 2 and not branch.private_goals for branch in branches))
        self.wires.clear()
        branches[0].run_task(self.task(3))
        wire = json.dumps(self.wires)
        self.assertNotIn("PRIVATE_SEED", wire)
        self.assertNotIn("HIDDEN_GOLD", wire)
        self.assertNotIn("FUTURE_SECRET", wire)
        self.assertEqual([item["role"] for item in self.wires], ["A", "B"])
        self.assertEqual(len(branches[0].protocol.commits), 3)
        self.assertEqual(len(branches[1].protocol.commits), 2)

    def test_malformed_final_is_failure_not_repaired_or_retried(self):
        calls = []
        def malformed(*args):
            calls.append(args)
            return {"text": "An explanation, without schema JSON.", "input_tokens": 20, "output_tokens": 10}
        branch = BranchRunner(Protocol(4, "session-one", {}), {"A": "Planner", "B": "Reviewer"}, self.settings(), malformed,
                              base={"name": "Base", "query": "Public", "daily_plans": []})
        result = branch.run_task(self.task(1))
        self.assertEqual(result["status"], "protocol_invalid")
        self.assertEqual(result["evaluation"]["H"], 0)
        self.assertEqual(result["evaluation"]["U"], 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(branch.protocol.commits)

    def test_timeout_attempt_is_accounted_and_result_persisted_before_phase_stop(self):
        spent, saved = [], {}
        def timeout(role, messages, schema, logical_id, deadline, remaining):
            spent.append(logical_id)
            raise ServiceFailure("Injected transport timeout", kind="timeout")
        def accounting(logical_ids):
            actual = [item for item in spent if item in logical_ids]
            return {"attempts": len(actual), "accounted_input_output_tokens": 5000 * len(actual), "unknown_spend_requests": actual}
        branch = BranchRunner(Protocol(4, "session-one", {}), {"A": "Planner", "B": "Reviewer"}, self.settings(), timeout,
                              base={"name": "Base", "query": "Public", "daily_plans": []},
                              accounting=accounting, persist=saved.__setitem__)
        with self.assertRaises(ServiceFailure):
            branch.run_task(self.task(1))
        result = saved["task-1"]
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["input_output_tokens"], 5000)
        self.assertEqual(result["unknown_spend_requests"], spent)
        self.assertEqual(result["evaluation"]["H"], 0)


if __name__ == "__main__":
    unittest.main()
