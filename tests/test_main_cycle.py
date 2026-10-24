"""Main orchestration checks using invented tasks and responses, never native IDs."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from collusion_handoff.abe_runner import ServiceFailure
from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.main_cycle import assigned_opportunities, approved_pipeline, continuation_group, verify_annotations
from collusion_handoff.main_manifest import ManifestError
import test_abe_continuation as synthetic_continuation
from test_main_manifest import synthetic_packet


class MainCycleTests(unittest.TestCase):
    def fixture(self, root):
        helper = synthetic_continuation.ContinuationTests()
        prefix, packages, config, group, catalog = helper.setup_scenario(root)
        manifest, _, _ = synthetic_packet()
        manifest["sample"]["group_ids"] = [404]
        manifest["sample"]["prefix_condition_by_group"] = {"404": "private_goal_induction"}
        manifest["sample"]["assigned_group_count"] = 1
        return helper, prefix, packages, config, group, catalog, manifest

    def test_all_six_actual_branches_survive_conditioned_analysis_population(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper, prefix, packages, config, group, catalog, manifest = self.fixture(root)
            manifest["science"]["primary_population"] = "confirmed_prefix_coalitions"
            before = (prefix / "snapshot.json").read_bytes()
            with helper.patches(helper.reply):
                result = continuation_group(prefix, packages, manifest, config, {"generation": {"seed": 42}},
                                            None, root / "work", group_registry=group, catalog=catalog)
            self.assertEqual((prefix / "snapshot.json").read_bytes(), before)
            final = read_json(result / "status.json")
            self.assertEqual(len(final["opportunities"]), 12)
            self.assertTrue(all(row["status"] == "committed" for row in final["opportunities"]))
            starts = read_json(result / "admin-start-states.json")
            self.assertEqual(len(starts), 6)
            self.assertEqual(len({fingerprint({key: value for key, value in row.items() if key != "branch_id"}) for row in starts}), 1)
            for row in final["opportunities"]:
                job = result / row["opaque_branch_id"]
                score = read_json(job / "results" / (row["task_id"] + ".json"))
                self.assertEqual((score["evaluation"]["H"], score["evaluation"]["U"], score["evaluation"]["Q"]), (0, 1, 1))
                wire = str(read_json(job / "actual-requests.json"))
                for marker in ("SEED_ONLY", "ORIGIN_PRIVATE_SEED", "GOLD_MUST_NOT", "FUTURE_MUST_NOT"):
                    self.assertNotIn(marker, wire)
                self.assertIn("Real generated peer history", wire)

    def test_first_transport_failure_preserves_all_assigned_results_without_retry(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper, prefix, packages, config, group, catalog, manifest = self.fixture(root)
            calls = []
            def fail(*args):
                calls.append(args)
                raise ServiceFailure("Synthetic timeout", kind="timeout")
            with helper.patches(fail), self.assertRaises(ServiceFailure):
                continuation_group(prefix, packages, manifest, config, {"generation": {"seed": 42}},
                                   None, root / "work", group_registry=group, catalog=catalog)
            final = read_json(root / "work/continuations/group-404/status.json")
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(final["opportunities"]), 12)
            self.assertEqual(sum(row["status"] == "timeout" for row in final["opportunities"]), 1)
            self.assertEqual(sum(row["status"] == "not_run_after_phase_failure" for row in final["opportunities"]), 11)

    def test_missing_memory_has_no_fallback_and_keeps_the_denominator(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper, prefix, packages, config, group, catalog, manifest = self.fixture(root)
            packages.pop("CVP")
            with helper.patches(helper.reply):
                result = continuation_group(prefix, packages, manifest, config, {"generation": {"seed": 42}},
                                            None, root / "work", group_registry=group, catalog=catalog)
            rows = read_json(result / "status.json")["opportunities"]
            self.assertEqual(len(rows), 12)
            self.assertEqual(sum(row["status"] == "not_run_missing_assigned_memory" for row in rows), 2)
            self.assertEqual(len(helper.calls), 20)

    def test_denominator_uses_explicit_main_ids_in_both_conditions(self):
        manifest, _, _ = synthetic_packet()
        groups = {number: {"tasks": [{"task_id": f"synthetic-{number}-{index}"} for index in range(1, 5)]}
                  for number in (998, 999)}
        before = deepcopy(manifest)
        rows = assigned_opportunities(manifest, groups)
        self.assertEqual(len(rows), 24)
        self.assertEqual({row["group_id"] for row in rows}, {998, 999})
        self.assertEqual({row["prefix_condition"] for row in rows}, {"nominal", "private_goal_induction"})
        self.assertEqual(manifest, before)

    def test_missing_main_approval_fails_before_config_or_native_source_is_opened(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _, ready = synthetic_packet()
            atomic_json(root / "main-manifest.json", manifest)
            atomic_json(root / "human-main-approval.json", {})
            atomic_json(root / "main-readiness.json", ready)
            with patch("collusion_handoff.main_cycle.file_hash", side_effect=AssertionError("Opened pipeline before approval")):
                with self.assertRaises(ManifestError):
                    approved_pipeline(root, root / "nonexistent-code")

    def test_AI_annotation_cannot_satisfy_main_human_gate(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root / "human-source-annotations.json", {
                "schema": "human_main_source_annotations_v1", "role": "assistant",
                "message_verified_from_app": False, "source_only_before_memory": True})
            with self.assertRaises(ManifestError):
                verify_annotations(root, {}, {}, root)


if __name__ == "__main__":
    unittest.main()
