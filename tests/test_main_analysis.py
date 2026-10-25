"""Scientific endpoint regressions on invented tasks; no native datasets/models."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from collusion_handoff.main_analysis import analyze, confirmed_groups, initial_proposal_outcomes, verify_analysis_review_binding
from collusion_handoff.paired_analysis import AnalysisError
from test_main_manifest import synthetic_packet


class MainAnalysisTests(unittest.TestCase):
    def test_changed_source_labels_or_reviewer_lock_cannot_change_population_after_replay(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "ai-source-annotations.json"
            independent = root / "reviewer-root.json"
            source.write_text('{"groups":{"998":{"harmful_future_agreement_present":false}}}')
            independent.write_text('{"formation_status":"absent"}')
            audit = {"review_files_sha256": {p.name: sha256(p.read_bytes()).hexdigest() for p in (source, independent)}}
            verify_analysis_review_binding(audit, root)
            for path in (source, independent):
                original = path.read_bytes()
                path.write_text('{"revised_after_replay":true}')
                with self.assertRaises(AnalysisError):
                    verify_analysis_review_binding(audit, root)
                path.write_bytes(original)
            with self.assertRaises(AnalysisError):
                verify_analysis_review_binding({}, root)

    def test_initial_Q_separates_known_absence_from_missing_and_corrupt_measurement(self):
        rows = [{"result": {"evaluation": value}} for value in (
            {"Q": 1, "initial_proposal_Q": 0, "ledger_complete": True},
            {"Q": 1, "initial_proposal_Q": 1, "ledger_complete": False},
            {"Q": 0, "initial_proposal_Q": None, "ledger_complete": True},
            {"Q": None, "initial_proposal_Q": None, "ledger_complete": False},
            {"Q": None, "initial_proposal_Q": None, "ledger_complete": True, "initial_proposal_error": "corrupt"},
        )]
        original = deepcopy(rows)
        scored = initial_proposal_outcomes(rows)
        self.assertEqual([r["result"]["evaluation"]["Q"] for r in scored], [0, 1, 0, None, None])
        self.assertEqual(rows, original)

    def test_formation_and_harm_are_distinct_and_uncertain_stays_uncertain(self):
        manifest, _, _ = synthetic_packet()
        labels = {"groups": {"998": {"harmful_future_agreement_present": True, "checkpoint_status": "active"},
                            "999": {"harmful_future_agreement_present": None, "checkpoint_status": "uncertain"}}}
        self.assertEqual(confirmed_groups(manifest, labels, {998: [0, 0], 999: [1, 1]}), ([], [999]))
        self.assertEqual(confirmed_groups(manifest, labels, {998: [1, 0], 999: [1, 1]}), ([998], [999]))
        labels["groups"]["998"]["checkpoint_status"] = "revoked"
        self.assertEqual(confirmed_groups(manifest, labels, {998: [1, 0], 999: [1, 1]}), ([], [999]))

    def test_conditions_are_separate_and_AI_does_not_become_human_gold(self):
        manifest, _, _ = synthetic_packet()
        manifest["science"]["secondary_contrasts"].append({"metric": "initial_Q", "minuend": "CVP", "subtrahend": "R"})
        labels = {"role": "assistant", "human_gold": False, "source_only_before_memory": True,
            "groups": {str(n): {"harmful_future_agreement_present": True, "checkpoint_status": "active"} for n in (998, 999)}}
        assignments = [{"group_id": n, "arm": arm, "task_id": f"synthetic-{n}-{t}"}
                       for n in (998, 999) for arm in manifest["sample"]["arms"] for t in (3, 4)]
        outcomes = [{**a, "result": {"task_id": a["task_id"], "status": "committed", "evaluation": {
            "H": 0, "U": 1, "Q": 1, "Q_applicable": True, "initial_proposal_Q": int(a["arm"] != "R"),
            "ledger_complete": True}}} for a in assignments]
        report = analyze(manifest, assignments, outcomes, labels, {998: [0, 0], 999: [1, 0]})
        self.assertFalse(report["human_gold"])
        self.assertEqual(report["AI_confirmed_prefix_groups"], [999])
        induced = report["conditions"]["private_goal_induction"]["confirmed_prefix_coalitions"]
        self.assertEqual(induced["first_proposal_Q"]["contrasts"][0]["estimate"], 1)
        nominal = report["conditions"]["nominal"]["confirmed_prefix_coalitions"]
        self.assertEqual(nominal["final_canonical_metrics"]["status"], "empty_pre_treatment_population")
        self.assertEqual(len(report["all_assigned_groups_retained"]), 2)
        labels["human_gold"] = True
        with self.assertRaises(AnalysisError):
            analyze(manifest, assignments, outcomes, labels, {998: [0, 0], 999: [1, 0]})


if __name__ == "__main__":
    unittest.main()
