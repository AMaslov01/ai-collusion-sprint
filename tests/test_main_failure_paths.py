"""Missing main measurements retain the assigned denominator and unknown outcomes."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from collusion_handoff.abe_cycle import namespace
from collusion_handoff.abe_runner import ServiceFailure
from collusion_handoff.artifacts import append_event, atomic_json, read_json
from collusion_handoff.main_cycle import initialize_phase_assignments, finalize_phase_assignments, continuation_group
import test_main_cycle as synthetic_main


class MainFailurePathTests(unittest.TestCase):
    def assignment(self):
        manifest = {"sample": {"group_ids": [404], "arms": ["F", "S", "C", "CV", "CVP", "R"],
                    "future_task_indices": [3, 4], "prefix_condition_by_group": {"404": "private_goal_induction"}}}
        groups = {404: {"tasks": [{"task_id": f"synthetic-t{index}"} for index in range(1, 5)]}}
        return manifest, groups, {"run_id": "synthetic-main"}

    def test_service_failure_before_client_keeps_prefix_and_all_future_tasks_unknown(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, groups, config = self.assignment()
            (root / "ledger").mkdir()
            (root / "ledger/requests.jsonl").write_bytes(b"")
            work = root / "prefix-work"
            initialize_phase_assignments(manifest, groups, work, "prefixes")
            self.assertEqual(read_json(work / "all-assigned-results.json")["assigned"], 12)
            self.assertEqual(len(read_json(work / "assigned-prefix-opportunities.json")), 2)
            finalize_phase_assignments(config, root, work, "prefixes", "Synthetic model startup failure")
            for name, count in (("all-assigned-results.json", 12), ("prefix-assigned-results.json", 2)):
                report = read_json(work / name)
                self.assertEqual(report["assigned"], count)
                for item in report["observed"]:
                    self.assertEqual(item["result"]["evaluation"], {"H": None, "U": None, "Q": None})
                    self.assertEqual(item["result"]["model_calls"], 0)
                    self.assertEqual(item["result"]["reason"], "Synthetic model startup failure")

    def test_corrupt_journal_keeps_original_cause_durable_result_and_unknown_remaining_counts(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, groups, config = self.assignment()
            (root / "ledger").mkdir()
            ledger = root / "ledger/requests.jsonl"
            ledger.write_bytes(b'{"logical_call_id":')
            work = root / "work"
            initialize_phase_assignments(manifest, groups, work, "continuations")
            branch = namespace(config["run_id"], 404, "recipient/R")
            durable = {"task_id": "synthetic-t4", "status": "synthetic_durable_commit", "evaluation": {"H": 1}}
            atomic_json(work / f"continuations/group-404/{branch}/results/synthetic-t4.json", durable)
            finalize_phase_assignments(config, root, work, "continuations", "Original transport interruption")
            report = read_json(work / "all-assigned-results.json")
            self.assertEqual(report["assigned"], 12)
            self.assertIn("Truncated", report["accounting_error"])
            for row in report["observed"]:
                if row["arm"] == "R" and row["task_id"] == "synthetic-t4":
                    self.assertEqual(row["result"], durable)
                else:
                    self.assertEqual(row["result"]["status"], "measurement_corruption")
                    self.assertIsNone(row["result"]["model_calls"])
                    self.assertEqual(row["result"]["evaluation"], {"H": None, "U": None, "Q": None})
            self.assertEqual(ledger.read_bytes(), b'{"logical_call_id":')

    def test_request_without_durable_terminal_result_never_becomes_zero_harm(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper, prefix, packages, config, group, catalog, manifest = synthetic_main.MainCycleTests().fixture(root)
            def interrupt(runner, task):
                append_event(root / "ledger/requests.jsonl", {
                    "logical_call_id": runner.protocol.branch_id + "/" + task["task_id"] + "/A/cycle-1/call-1"})
                raise ServiceFailure("Synthetic interruption after request append")
            with helper.patches(helper.reply), patch("collusion_handoff.abe_runner.BranchRunner.run_task", autospec=True, side_effect=interrupt):
                with self.assertRaisesRegex(ServiceFailure, "Synthetic interruption"):
                    continuation_group(prefix, packages, manifest, config, {"generation": {"seed": 42}},
                        SimpleNamespace(root=root), root / "work", group_registry=group, catalog=catalog)
            status = read_json(root / "work/continuations/group-404/status.json")
            self.assertEqual(len(status["opportunities"]), 12)
            self.assertEqual(sum(row["status"] == "interrupted_before_durable_task_result" for row in status["opportunities"]), 1)
            for row in status["opportunities"]:
                branch = namespace(config["run_id"], 404, "recipient/" + row["arm"])
                result = read_json(root / f"work/continuations/group-404/{branch}/results" / (row["task_id"] + ".json"))
                self.assertEqual(result["evaluation"], {"H": None, "U": None, "Q": None})


if __name__ == "__main__":
    unittest.main()
