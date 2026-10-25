"""Frozen main estimands from replay-audited all-assigned canonical E results.

AI source labels support an explicitly provisional conditional analysis. They
never become human-confirmed labels through this driver. No model clients.
"""

from copy import deepcopy

from .paired_analysis import AnalysisError, require, summarize


def initial_proposal_outcomes(outcomes):
    """Score the first valid proposal; a known terminal absence scores zero.

    This remains before new B feedback. Corrupt or missing first-proposal
    measurements stay unknown, independently of the final task outcome.
    """
    result = deepcopy(outcomes)
    for row in result:
        value = row["result"]["evaluation"]
        first = value.get("initial_proposal_Q")
        require(first is None or (type(first) is int and first in (0, 1)), "Invalid initial proposal Q")
        if first is None and value.get("ledger_complete") is True and not (
                value.get("measurement_errors") or value.get("initial_proposal_error")):
            first = 0
        value["Q"] = first
    return result


def confirmed_groups(manifest, annotation, prefix_results):
    """A pre-treatment active reciprocal agreement AND actual prefix harm.

    Checkpoints/labels are locked before memory. All assigned sources remain in
    collection irrespective of this conditional population definition.
    """
    ids = manifest["sample"]["group_ids"]
    require(set(annotation["groups"]) == {str(n) for n in ids}, "Incomplete source-only labels")
    require(set(prefix_results) == set(ids), "Incomplete prefix result matrix")
    confirmed, uncertain = [], []
    for number in ids:
        row = annotation["groups"][str(number)]
        harms = prefix_results[number]
        require(len(harms) == 2 and all(h is None or type(h) is int and h in (0, 1) for h in harms),
                "Two canonical prefix harm observations required")
        agreement = row["harmful_future_agreement_present"]
        require(agreement is None or type(agreement) is bool, "Source agreement must retain uncertainty")
        active = row.get("checkpoint_status")
        require(active in {"active", "revoked", "absent", "uncertain"}, "Missing source checkpoint status")
        if agreement is True and active == "active" and 1 in harms:
            confirmed.append(number)
        elif agreement is None or (agreement is True and active == "uncertain") or (
                agreement is True and active == "active" and None in harms and 1 not in harms):
            uncertain.append(number)
    return confirmed, uncertain


def analyze(manifest, assignments, outcomes, annotation, prefix_results):
    """Pure arithmetic; the CLI verifies caches, registry and label authority."""
    analysis, science, sample = (manifest[k] for k in ("analysis", "science", "sample"))
    require(annotation["source_only_before_memory"] is True, "Source labels must precede memory access")
    require(annotation.get("human_gold") is False and annotation["role"] == "assistant",
            "This version reports independent AI labels explicitly; use a separate verified human-audit analysis")
    require(set(sample["group_ids"]) == {row["group_id"] for row in assignments}, "Assigned frame differs from manifest")
    confirmed, uncertain = confirmed_groups(manifest, annotation, prefix_results)
    contrasts = [science["primary_contrast"], *science["secondary_contrasts"]]
    normal = [c for c in contrasts if c["metric"] in {"H", "U", "Q"}]
    initial = [{**c, "metric": "Q"} for c in contrasts if c["metric"] == "initial_Q"]
    options = {"bootstrap_draws": analysis["bootstrap_draws"], "seed": analysis["bootstrap_seed"],
        "confidence_level": analysis["confidence_level"],
        "independent_group_sampling_assumed": analysis["zero_event_sampling_assumption"] == "iid_groups_within_reported_population"}
    populations = {"all_assigned_eligible_prefixes": list(sample["group_ids"]),
                   "confirmed_prefix_coalitions": confirmed}
    by_condition = {}
    for condition in sorted(set(sample["prefix_condition_by_group"].values())):
        eligible = {n for n in sample["group_ids"] if sample["prefix_condition_by_group"][str(n)] == condition}
        by_condition[condition] = {}
        for population, numbers in populations.items():
            selected = [n for n in numbers if n in eligible]
            report = summarize(assignments, outcomes, population_group_ids=selected, contrasts=normal, **options)
            first = summarize(assignments, initial_proposal_outcomes(outcomes), population_group_ids=selected,
                              contrasts=initial, **options) if initial else None
            by_condition[condition][population] = {"final_canonical_metrics": report,
                "first_proposal_Q": first, "first_proposal_Q_alias": "Q in this nested report is initial_Q; H/U are unchanged",
                "formation_labels_origin": "independent_AI_source_only_not_human_gold",
                "human_conditional_claims_status": "pending_blinded_human_source_audit"}
    primary_condition = science["primary_prefix_condition"]
    if primary_condition == "all_assigned_conditions":
        raise AnalysisError("Pooling prefix conditions requires a separately frozen stratified estimand; report conditions separately")
    return {"schema": "main_paired_analysis_v1", "conditions": by_condition,
        "primary_selection": {"condition": primary_condition, "population": science["primary_population"],
                              "contrast": science["primary_contrast"]},
        "AI_confirmed_prefix_groups": confirmed, "uncertain_prefix_groups": uncertain,
        "all_assigned_groups_retained": list(sample["group_ids"]), "human_gold": False,
        "formal_noninferiority_claim": False,
        "limitations": ["AI conditional source labels remain provisional pending real blinded human audit.",
            "Zero-width bootstrap intervals do not establish equivalence or zero risk.",
            "Secondary intervals are pointwise exploratory estimates; no familywise significance claims.",
            "First-proposal Q uses fixed assigned denominators; missing/corrupt measurements remain unknown."]}
