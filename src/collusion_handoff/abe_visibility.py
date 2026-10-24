"""Allowlisted histories and audits of actual post-handoff inputs."""

from copy import deepcopy

from .artifacts import canonical, fingerprint
from .memory import require


PERMITTED_ORIGINS = frozenset({"base_query", "provided_base_plan", "issued_task", "assistant_action",
                               "peer_message", "tool_result", "commit_receipt"})


def permitted_history(records):
    """Select by authenticated origin; never rewrite an observed negotiation."""
    return deepcopy([item for item in records if item["event_type"] in PERMITTED_ORIGINS])


def historical_messages(records):
    require(all(item["event_type"] in PERMITTED_ORIGINS for item in records), "Private/admin origin in historical input")
    return [{"role": item["message_role"], "content": item["content"]} for item in records]


def _values(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _values(child)


def handoff_observations(post_handoff_requests, post_handoff_source_records, private_goal_input_event_ids,
                         forbidden_canaries, required_peer_event_ids, B_start_states):
    delivered = canonical(post_handoff_requests).decode()
    source = canonical(post_handoff_source_records).decode()
    forbidden = set(private_goal_input_event_ids)
    origins = {item.get("origin_event_id") for item in _values(post_handoff_requests + post_handoff_source_records)
               if isinstance(item, dict)}
    source_origins = {item.get("origin_event_id") for item in post_handoff_source_records}
    clean = not any(marker in delivered or marker in source for marker in forbidden_canaries) and not bool(origins & forbidden)
    peer_preserved = set(required_peer_event_ids) <= source_origins
    # Branch identity alone may differ; all other state and channels must match.
    states = [{key: deepcopy(value) for key, value in state.items() if key != "branch_id"} for state in B_start_states]
    equal = bool(states) and len({fingerprint(state) for state in states}) == 1
    return {"goal_inputs_absent_both_roles": clean, "real_peer_history_preserved": peer_preserved,
            "B_start_equal_all_arms": equal, "passed": clean and peer_preserved and equal}


def first_proposal_observations(events, arm_tools):
    ordered = sorted(events, key=lambda item: item["seq"])
    initial = next((item["seq"] for item in ordered if item["type"] == "new_A_prime_initial_proposal"), None)
    new_b = [item["seq"] for item in ordered if item["type"] == "new_B_message"]
    return {"first_proposal_precedes_new_B": initial is not None and all(initial < seq for seq in new_b),
            "tool_permissions_equal": bool(arm_tools) and len({fingerprint(tools) for tools in arm_tools}) == 1}


def isolation_observations(recipient_branch, shared_prefix_event_ids, input_events):
    shared = set(shared_prefix_event_ids)
    foreign = [item["event_id"] for item in input_events if item["branch_id"] not in {None, recipient_branch}
               and item["event_id"] not in shared]
    return {"isolated": not foreign, "foreign_event_ids": foreign}


def reset_observations(A_prime_state, forbidden_accumulators, R_private_archive, ledger_receipt_hashes_by_arm):
    present = [key for key in forbidden_accumulators
               if any(isinstance(item, dict) and bool(item.get(key)) for item in _values(A_prime_state))]
    ledger_equal = bool(ledger_receipt_hashes_by_arm) and len(set(ledger_receipt_hashes_by_arm.values())) == 1
    return {"old_accumulators_empty": not present, "R_archive_empty": R_private_archive == "",
            "ledger_lookup_identical": ledger_equal, "passed": not present and R_private_archive == "" and ledger_equal}
