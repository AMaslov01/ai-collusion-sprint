"""Frozen main estimands from replay-audited all-assigned canonical E results.

AI source labels support an explicitly provisional conditional analysis. They
never become human-confirmed labels through this driver. No model clients.
"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from .artifacts import read_json
from .paired_analysis import AnalysisError, require, summarize


def verify_phase_preflight_binding(actual_phase_path, preflight_path, manifest_sha256, phase):
    actual = read_json(actual_phase_path)
    require(actual["manifest_sha256"] == manifest_sha256 and actual["phase"] == phase
            and actual["CPU_preflight_sha256"] == sha256(Path(preflight_path).read_bytes()).hexdigest(),
            "Supplied CPU receipt differs from the receipt recorded by the actual inference phase")


def verify_analysis_run_binding(audit, root, required_paths):
    inventory = audit.get("input_sha256")
    require(isinstance(inventory, dict) and required_paths and set(required_paths) <= set(inventory),
            "Cache audit omits required canonical analysis inputs")
    root = Path(root).resolve()
    for name, digest in inventory.items():
        require(isinstance(name, str) and name and not Path(name).is_absolute() and ".." not in Path(name).parts,
                "Invalid analysis input path")
        path = root / name
        require(path.resolve().is_relative_to(root) and path.is_file()
                and sha256(path.read_bytes()).hexdigest() == digest, "Canonical analysis input changed after replay")


def verify_executing_analysis_driver(path, expected_sha256):
    require(sha256(Path(path).read_bytes()).hexdigest() == expected_sha256,
            "Executing analysis driver differs from the frozen analysis implementation")


def verify_analysis_review_binding(audit, review):
    """Keep the original source-only population mask after cache replay."""
    inventory = audit.get("review_files_sha256")
    require(isinstance(inventory, dict) and inventory, "Replay must bind the original collection review inventory")
    root = Path(review).resolve()
    for name, digest in inventory.items():
        require(isinstance(name, str) and name and not Path(name).is_absolute()
                and ".." not in Path(name).parts and name != ".",
                "Review inventory must contain confined relative paths")
        path = root / name
        require(path.resolve().is_relative_to(root) and path.is_file()
                and sha256(path.read_bytes()).hexdigest() == digest,
                "Source labels/reviewer/reference packet changed after production cache replay")


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
