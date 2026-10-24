"""All-assigned paired group summaries; population selection is an external freeze.

Inputs have one prefix per source group and post-handoff H/U/Q opportunities,
with a nonempty Q predicate for every assigned task. This module consumes canonical E results; it never infers an outcome
from text, repairs an E label, drops a failed task, or selects a favorable subgroup.
"""

from collections import Counter
import math

import numpy as np


class AnalysisError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AnalysisError(message)


def key(row):
    require(isinstance(row, dict) and {"group_id", "arm", "task_id"} <= set(row),
            "Missing opportunity identity")
    group, arm, task = row["group_id"], row["arm"], row["task_id"]
    require(type(group) is int and group >= 0 and isinstance(arm, str) and arm
            and isinstance(task, str) and task, "Invalid assigned opportunity identity")
    return group, arm, task


def zero_event_upper(groups, confidence):
    """One-sided exact bound for zero events in IID Bernoulli GROUP indicators."""
    require(type(groups) is int and groups > 0 and 0 < confidence < 1, "Invalid zero-event bound")
    return -math.expm1(math.log1p(-confidence) / groups)


def summarize(assignments, outcomes, *, population_group_ids, contrasts, bootstrap_draws,
              seed, confidence_level, independent_group_sampling_assumed=False):
    """Preserve fixed task denominators; bootstrap identical group indices for all arms.

    `assignments` is the immutable pre-branching registry. `outcomes` may be an
    incomplete list of rows with the same identity and `result.evaluation`.
    Missing rows/None E values remain unknown and receive [0,1] identification
    bounds. `population_group_ids` must come from pre-treatment selection; its
    correctness/authority is verified by the calling frozen analysis driver.
    """
    require(type(bootstrap_draws) is int and bootstrap_draws >= 2, "Specify bootstrap draws")
    require(type(seed) is int and seed >= 0, "Specify a nonnegative bootstrap seed")
    require(type(confidence_level) in (int, float) and math.isfinite(confidence_level)
            and 0 < confidence_level < 1, "Specify confidence level")
    require(type(independent_group_sampling_assumed) is bool, "Specify sampling assumption explicitly")
    require(isinstance(assignments, list) and isinstance(outcomes, list), "Use explicit assigned/result lists")
    expected = [key(row) for row in assignments]
    require(expected and len(set(expected)) == len(expected), "Missing or duplicate assigned opportunities")
    groups, arms = sorted({k[0] for k in expected}), sorted({k[1] for k in expected})
    require(len(arms) >= 2, "At least two assigned arms required")
    by_group = {}
    for group in groups:
        tasks = {arm: {k[2] for k in expected if k[:2] == (group, arm)} for arm in arms}
        require(all(tasks.values()) and all(tasks[a] == tasks[arms[0]] for a in arms),
                "All arms need the same fixed tasks within each group")
        by_group[group] = sorted(tasks[arms[0]])
    require(isinstance(population_group_ids, (list, tuple))
            and all(type(g) is int for g in population_group_ids)
            and len(set(population_group_ids)) == len(population_group_ids)
            and set(population_group_ids) <= set(groups), "Invalid pre-treatment population")
    selected = sorted(population_group_ids)
    expected_set, actual = set(expected), {}
    for row in outcomes:
        identity = key(row)
        require(identity in expected_set and identity not in actual, "Unassigned or duplicate observed result")
        require(isinstance(row.get("result"), dict) and isinstance(row["result"].get("evaluation"), dict),
                "Missing typed E result")
        evaluation = row["result"]["evaluation"]
        require(row["result"].get("task_id", identity[2]) == identity[2]
                and evaluation.get("task_id", identity[2]) == identity[2], "Result task identity differs")
        require(evaluation.get("Q_applicable", True) is True, "This analysis requires fixed applicable Q predicates")
        for metric in ("H", "U", "Q"):
            require(evaluation.get(metric) is None or
                    (type(evaluation[metric]) is int and evaluation[metric] in (0, 1)), "Nonbinary E result")
        actual[identity] = row["result"]
    require(isinstance(contrasts, list) and contrasts, "Specify contrasts before analysis")
    for contrast in contrasts:
        require(set(contrast) == {"metric", "minuend", "subtrahend"}
                and contrast["metric"] in ("H", "U", "Q")
                and contrast["minuend"] in arms and contrast["subtrahend"] in arms
                and contrast["minuend"] != contrast["subtrahend"], "Invalid frozen contrast")
    report = {"schema": "all_assigned_paired_group_analysis_v1", "cluster_unit": "source_group",
              "population_group_ids": selected, "assigned_frame_group_ids": groups,
              "bootstrap_draws": bootstrap_draws, "seed": seed, "confidence_level": confidence_level,
              "quantile_method": "numpy.linear", "rng": "numpy.PCG64", "numpy_version": np.__version__,
              "independent_group_sampling_assumed": independent_group_sampling_assumed,
              "primary_denominator_policy": "One prefix per group; all assigned tasks, macro mean over source groups",
              "status": "descriptive_and_paired_bootstrap" if selected else "empty_pre_treatment_population",
              "arms": {}, "contrasts": [], "warnings": []}
    if not selected:
        report["warnings"].append("No eligible source group; no efficacy estimate or bootstrap is defined.")
        return report
    if len(selected) < 10:
        report["warnings"].append("Very few independent groups; bootstrap precision and tail coverage are unreliable.")
    indices = np.random.Generator(np.random.PCG64(seed)).integers(0, len(selected), size=(bootstrap_draws, len(selected)))
    tail = (1 - confidence_level) / 2
    bounds = {}

    def interval(values):
        return np.quantile(values, [tail, 1 - tail], method="linear").tolist()

    def estimate(lower, upper):
        complete = bool(np.array_equal(lower, upper))
        low_draws, high_draws = lower[indices].mean(axis=1), upper[indices].mean(axis=1)
        point = float(lower.mean()) if complete else None
        ci = interval(low_draws) if complete else None
        return {"estimate": point, "identification_bounds": [float(lower.mean()), float(upper.mean())],
                "paired_group_percentile_CI": ci,
                "identification_endpoint_bootstrap_CIs": None if complete else {
                    "lower_endpoint": interval(low_draws), "upper_endpoint": interval(high_draws)},
                "degenerate_bootstrap": bool(complete and np.all(low_draws == low_draws[0])),
                "complete_outcomes": complete}

    for arm in arms:
        arm_rows = [actual.get((g, arm, task)) for g in selected for task in by_group[g]]
        item = {"assigned_tasks": len(arm_rows), "independent_groups": len(selected),
                "statuses": dict(Counter(row.get("status", "unspecified") if row else "missing_result_unknown"
                                         for row in arm_rows)), "metrics": {}}
        for metric in ("H", "U", "Q"):
            lower, upper, known_positive, unknown = [], [], 0, 0
            for group in selected:
                values = [actual.get((group, arm, task), {}).get("evaluation", {}).get(metric)
                          for task in by_group[group]]
                known_positive += sum(v == 1 for v in values)
                unknown += sum(v is None for v in values)
                lower.append(sum(v or 0 for v in values) / len(values))
                upper.append(sum(1 if v is None else v for v in values) / len(values))
            lo, hi = np.array(lower), np.array(upper)
            bounds[arm, metric] = lo, hi
            item["metrics"][metric] = {**estimate(lo, hi), "observed_positive_tasks": known_positive,
                                         "unknown_tasks": unknown}
        any_harm = []
        for group in selected:
            values = [actual.get((group, arm, task), {}).get("evaluation", {}).get("H") for task in by_group[group]]
            any_harm.append(1 if 1 in values else (None if None in values else 0))
        zero_observed = all(value == 0 for value in any_harm)
        item["group_any_harm"] = {"positive_groups": sum(v == 1 for v in any_harm),
                                  "unknown_groups": sum(v is None for v in any_harm),
                                  "all_groups_observed_zero": zero_observed,
                                  "zero_event_one_sided_upper": zero_event_upper(len(selected), confidence_level)
                                  if zero_observed and independent_group_sampling_assumed else None,
                                  "bound_applies_to": "Probability of any harm in an IID source group, not a task-level binomial sample"}
        report["arms"][arm] = item
    for contrast in contrasts:
        metric, minuend, subtrahend = (contrast[k] for k in ("metric", "minuend", "subtrahend"))
        left_lo, left_hi = bounds[minuend, metric]
        right_lo, right_hi = bounds[subtrahend, metric]
        result = {**contrast, **estimate(left_lo - right_hi, left_hi - right_lo)}
        if result["degenerate_bootstrap"]:
            report["warnings"].append(f"Degenerate {metric}:{minuend}-{subtrahend} bootstrap is not proof of equivalence or zero risk.")
        if metric == "H" and independent_group_sampling_assumed and all(
                report["arms"][arm]["group_any_harm"]["all_groups_observed_zero"] for arm in (minuend, subtrahend)):
            upper = zero_event_upper(len(selected), 1 - (1 - confidence_level) / 2)
            result["all_zero_conservative_difference_bound"] = {
                "interval": [-upper, upper], "confidence_level": confidence_level,
                "method": "Bonferroni two one-sided IID group-any bounds; mean task harm cannot exceed group-any probability"}
        report["contrasts"].append(result)
    report["warnings"].append("Identification bounds describe missing outcomes; their endpoint bootstrap intervals are not a confidence interval for a latent point effect.")
    report["warnings"].append("Population selection and canonical-E/cache integrity are verified outside this arithmetic module.")
    report["warnings"].append("Intervals are pointwise; no multiplicity-adjusted secondary significance decisions are implemented.")
    return report
