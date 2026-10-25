"""Assigned denominators survive service startup and early source-check failures."""

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from collusion_handoff import calibration_cycle as cycle
from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.memory import MemoryError
from test_abe_protocol import fixture_task


ARMS = ["F", "S", "C", "CV", "CVP", "R"]


class CalibrationFailurePathTests(unittest.TestCase):
    def path_mapper(self, preflight):
        def mapped(value):
            text = str(value)
            return preflight / text.removeprefix("/preflight").lstrip("/") if text.startswith("/preflight") else Path(value)
        return mapped

    def test_truncated_ledger_keeps_original_failure_durable_result_and_unknown_remaining_outcomes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, work, global_root = root / "preflight", root / "work", root / "global"
            assigned = [{"group_id": number, "traveler_index": index, "task_id": f"g{number}-t{index}"}
                        for number in (8, 9) for index in (1, 2)]
            atomic_json(preflight / "assigned-prefix-opportunities.json", assigned)
            durable = {"task_id": "g8-t1", "status": "synthetic_durable_result", "evaluation": {"H": 1, "U": 0, "Q": None}}
            atomic_json(work / "prefix/group-8/results/g8-t1.json", durable)
            (global_root / "ledger").mkdir(parents=True)
            ledger = global_root / "ledger/requests.jsonl"
            truncated = b'{"logical_call_id":'
            ledger.write_bytes(truncated)
            amendment = {"run_id": "synthetic-calibration", "phases": {"prefix": "synthetic-prefix"}}
            with patch.object(cycle, "Path", side_effect=self.path_mapper(preflight)), patch.object(cycle, "ROOT", global_root):
                cycle.initialize_assigned_outputs({"selected_worlds": [8, 9]}, work, prefix=True)
                cycle.record_supervisor_failure(amendment, "synthetic-prefix", work, "Original startup failure")
            report = read_json(work / "all-assigned-results.json")
            self.assertEqual(report["supervisor_failure"], "Original startup failure")
            self.assertIn("Truncated", report["accounting_error"])
            self.assertEqual(report["observed"][0]["result"], durable)
            for row in report["observed"][1:]:
                self.assertEqual(row["result"]["status"], "measurement_corruption")
                self.assertIsNone(row["result"]["model_calls"])
                self.assertEqual(row["result"]["evaluation"], {"H": None, "U": None, "Q": None})
                self.assertEqual(row["result"]["reason"], "Original startup failure")
            self.assertEqual(ledger.read_bytes(), truncated)

    def test_prefix_model_startup_failure_keeps_all_four_expected_tasks_and_reason(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, work, global_root = root / "preflight", root / "work", root / "global"
            (global_root / "ledger").mkdir(parents=True)
            (global_root / "ledger/requests.jsonl").write_bytes(b"")
            assigned = [{"group_id": number, "traveler_index": index, "task_id": f"g{number}-t{index}"}
                        for number in (8, 9) for index in (1, 2)]
            atomic_json(preflight / "assigned-prefix-opportunities.json", assigned)
            amendment = {"run_id": "synthetic-calibration", "phases": {"prefix": "synthetic-prefix"}}
            with patch.object(cycle, "Path", side_effect=self.path_mapper(preflight)), patch.object(cycle, "ROOT", global_root):
                cycle.initialize_assigned_outputs({"selected_worlds": [8, 9]}, work, prefix=True)
                cycle.record_supervisor_failure(amendment, "synthetic-prefix", work, "Injected model startup failure")
            report = read_json(work / "all-assigned-results.json")
            self.assertEqual(report["assigned"], 4)
            for row in report["observed"]:
                self.assertEqual(row["result"]["status"], "not_run_after_supervisor_failure")
                self.assertEqual(row["result"]["model_calls"], 0)
                self.assertIsNone(row["result"]["evaluation"]["H"])
                self.assertIn("Injected model startup failure", row["result"]["reason"])

    @unittest.skipUnless(importlib.util.find_spec("transformers"), "Full deployed CPU image required for client import")
    def test_handoff_selection_failure_preserves_twelve_and_does_not_mask_original_error(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight, work, global_root = root / "preflight", root / "work", root / "global"
            (global_root / "ledger").mkdir(parents=True)
            (global_root / "ledger/requests.jsonl").write_bytes(b"")
            tasks = []
            for index in range(1, 5):
                task = fixture_task(f"g9-t{index}")
                task.update(group_id=9, traveler_index=index, traveler_id=f"person-{index}", memory=None)
                task["checked_predicates"].remove("memory")
                if index > 2:
                    task["checked_predicates"].append("memory")
                    task["memory"] = {"question": "Synthetic source-only question", "expected": {"amount_min": "1", "amount_max": "2",
                        "approximate": False, "basis": "per_person", "currency_marker": "$"}, "source_refs": [{"event_id": "synthetic-source"}]}
                tasks.append(task)
            assigned = [{"group_id": 9, "arm": arm, "task_id": task["task_id"], "traveler_index": task["traveler_index"]}
                        for arm in ARMS for task in tasks[2:]]
            atomic_json(preflight / "assigned-future-opportunities.json", assigned)
            atomic_json(preflight / "selected-task-registry.json", {"groups": {"9": {"group_id": 9, "tasks": tasks}}})
            amendment = {"run_id": "synthetic-calibration", "phases": {"prefix": "synthetic-prefix", "handoff": "synthetic-handoff"},
                         "source_frame": {"ordered_ids": list(range(8, 20))}}
            effective = {"run_id": "synthetic-calibration", "task_policy": {"catalog_csv_sha256": "f" * 64}}
            pre = {"selected_worlds": [8, 9], "handoff_world": 9}
            http = MagicMock()
            http.__enter__.return_value = http
            budget = MagicMock()
            budget.state.return_value = {"attempts": 0}
            budget.child_state.return_value = {"attempts": 0}
            with patch.object(cycle, "Path", side_effect=self.path_mapper(preflight)), patch.object(cycle, "ROOT", global_root), \
                 patch.object(cycle, "WORK", work), patch.object(cycle, "phase_identity", return_value={"phase": "synthetic-handoff"}), \
                 patch.object(cycle, "CalibrationBudget", return_value=budget), patch.object(cycle, "verify_parent"), \
                 patch.object(cycle, "select_source", side_effect=MemoryError("Injected pre-memory selection failure")), \
                 patch("collusion_handoff.catalog.load_accommodations", return_value={}), patch("httpx.Client", return_value=http), \
                 patch("collusion_handoff.readiness_cycle.memory_job") as memory:
                with self.assertRaisesRegex(MemoryError, "Injected pre-memory selection failure"):
                    cycle.run_client(amendment, effective, {}, {}, {}, pre)
            memory.assert_not_called()
            http.post.assert_not_called()
            report = read_json(work / "all-assigned-results.json")
            self.assertEqual(report["assigned"], 12)
            self.assertEqual(len(report["observed"]), 12)
            self.assertTrue(all(row["result"]["status"] == "not_run_after_phase_failure" for row in report["observed"]))
            failure = read_json(work / "phase-status.json")["failure"]
            self.assertIn("Injected pre-memory selection failure", failure)
            self.assertNotIn("UnboundLocalError", failure)


if __name__ == "__main__":
    unittest.main()
