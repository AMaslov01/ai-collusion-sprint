"""Synthetic checks of scientific denominators, paired resampling and unknowns."""

from copy import deepcopy
import unittest

from collusion_handoff.paired_analysis import AnalysisError, summarize, zero_event_upper


CONTRAST = {"metric": "H", "minuend": "S", "subtrahend": "CVP"}


def fixture(tasks_by_group=None, value=None):
    tasks_by_group = tasks_by_group or {10: 2, 20: 2}
    assignments, results = [], []
    for group, count in tasks_by_group.items():
        for arm in ("S", "CVP"):
            for index in range(count):
                identity = {"group_id": group, "arm": arm, "task_id": f"g{group}-t{index}"}
                assignments.append(identity)
                h = value(group, arm, index) if value else 0
                results.append({**identity, "result": {"status": "committed", "task_id": identity["task_id"],
                    "evaluation": {"H": h, "U": 1 - h, "Q": 1, "Q_applicable": True}}})
    return assignments, results


def analyze(assignments, results, **kwargs):
    options = {"population_group_ids": sorted({row["group_id"] for row in assignments}),
               "contrasts": [CONTRAST], "bootstrap_draws": 200, "seed": 173, "confidence_level": 0.95}
    options.update(kwargs)
    return summarize(assignments, results, **options)


class PairedAnalysisTests(unittest.TestCase):
    def test_group_macro_does_not_silently_become_pooled_task_rate(self):
        assigned, results = fixture({10: 1, 20: 3}, lambda g, a, t: int(g == 10 and a == "S"))
        summary = analyze(assigned, results)
        self.assertEqual(summary["arms"]["S"]["assigned_tasks"], 4)
        self.assertEqual(summary["arms"]["S"]["metrics"]["H"]["observed_positive_tasks"], 1)
        self.assertEqual(summary["arms"]["S"]["metrics"]["H"]["estimate"], 0.5)
        self.assertEqual(summary["contrasts"][0]["estimate"], 0.5)

    def test_identical_arms_use_the_same_resampled_groups(self):
        assigned, results = fixture(value=lambda g, a, t: int(g == 20))
        summary = analyze(assigned, results)
        self.assertNotEqual(summary["arms"]["S"]["metrics"]["H"]["paired_group_percentile_CI"], [0, 0])
        self.assertEqual(summary["contrasts"][0]["paired_group_percentile_CI"], [0, 0])
        self.assertTrue(summary["contrasts"][0]["degenerate_bootstrap"])

    def test_unknown_stays_in_fixed_denominator_and_has_bounds_not_a_point_CI(self):
        assigned, results = fixture(value=lambda g, a, t: int(a == "S"))
        results = [row for row in results if not (row["group_id"] == 10 and row["arm"] == "S" and row["task_id"] == "g10-t0")]
        summary = analyze(assigned, results)
        metric = summary["arms"]["S"]["metrics"]["H"]
        contrast = summary["contrasts"][0]
        self.assertEqual(summary["arms"]["S"]["assigned_tasks"], 4)
        self.assertEqual(metric["unknown_tasks"], 1)
        self.assertEqual(contrast["identification_bounds"], [0.75, 1])
        self.assertIsNone(contrast["estimate"])
        self.assertIsNone(contrast["paired_group_percentile_CI"])
        self.assertIsNotNone(contrast["identification_endpoint_bootstrap_CIs"])
        self.assertEqual(summary["arms"]["S"]["group_any_harm"]["positive_groups"], 2)
        self.assertEqual(summary["arms"]["S"]["group_any_harm"]["unknown_groups"], 0)

    def test_known_protocol_failure_is_zero_utility_while_missing_state_is_unknown(self):
        assigned, results = fixture()
        row = results[0]["result"]
        row["status"] = "protocol_invalid"
        row["evaluation"].update(H=0, U=0, Q=0)
        summary = analyze(assigned, results)
        self.assertEqual(summary["arms"]["S"]["metrics"]["U"]["estimate"], 0.75)
        self.assertEqual(summary["arms"]["S"]["metrics"]["H"]["unknown_tasks"], 0)
        row["evaluation"].update(H=None, U=None, Q=None)
        summary = analyze(assigned, results)
        self.assertIsNone(summary["arms"]["S"]["metrics"]["H"]["estimate"])

    def test_zero_bootstrap_does_not_erase_positive_finite_risk_bound(self):
        assigned, results = fixture({g: 2 for g in range(4)})
        summary = analyze(assigned, results, independent_group_sampling_assumed=True)
        arm = summary["arms"]["S"]["group_any_harm"]
        contrast = summary["contrasts"][0]
        self.assertEqual(contrast["paired_group_percentile_CI"], [0, 0])
        self.assertGreater(arm["zero_event_one_sided_upper"], 0)
        self.assertAlmostEqual((1 - arm["zero_event_one_sided_upper"]) ** 4, 0.05)
        low, high = contrast["all_zero_conservative_difference_bound"]["interval"]
        self.assertLess(low, 0)
        self.assertGreater(high, 0)
        self.assertAlmostEqual((1 - high) ** 4, 0.025)
        no_assumption = analyze(assigned, results)
        self.assertIsNone(no_assumption["arms"]["S"]["group_any_harm"]["zero_event_one_sided_upper"])
        self.assertNotIn("all_zero_conservative_difference_bound", no_assumption["contrasts"][0])

    def test_empty_pre_treatment_population_has_no_effect_or_bootstrap(self):
        assigned, results = fixture()
        summary = analyze(assigned, results, population_group_ids=[])
        self.assertEqual(summary["status"], "empty_pre_treatment_population")
        self.assertFalse(summary["arms"])
        self.assertFalse(summary["contrasts"])

    def test_duplicate_unassigned_or_incomplete_arm_matrices_fail(self):
        assigned, results = fixture()
        bad = [
            (assigned + [assigned[0]], results),
            (assigned, results + [results[0]]),
            (assigned[1:], results[1:]),
            (assigned, [*results, {**results[0], "task_id": "unassigned"}]),
        ]
        for assignments, outcomes in bad:
            with self.subTest(assignments=len(assignments), outcomes=len(outcomes)), self.assertRaises(AnalysisError):
                analyze(assignments, outcomes)

    def test_wrong_or_nonbinary_E_identity_is_not_silently_coerced(self):
        assigned, results = fixture()
        for replacement in (True, 0.5, "0", float("nan")):
            changed = deepcopy(results)
            changed[0]["result"]["evaluation"]["H"] = replacement
            with self.subTest(value=replacement), self.assertRaises(AnalysisError):
                analyze(assigned, changed)
        results[0]["result"]["task_id"] = "other-task"
        with self.assertRaises(AnalysisError):
            analyze(assigned, results)

    def test_inputs_are_not_mutated_and_seed_is_reproducible(self):
        assigned, results = fixture(value=lambda g, a, t: int(g == 20 and a == "S"))
        before = deepcopy((assigned, results))
        self.assertEqual(analyze(assigned, results), analyze(assigned, results))
        self.assertEqual((assigned, results), before)
        self.assertGreater(zero_event_upper(40, 0.95), zero_event_upper(80, 0.95))


if __name__ == "__main__":
    unittest.main()
