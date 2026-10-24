from copy import deepcopy
import unittest

from collusion_handoff.abe_visibility import permitted_history, handoff_observations, first_proposal_observations, isolation_observations


class VisibilityTests(unittest.TestCase):
    def test_private_inputs_removed_by_origin_real_negotiation_unchanged(self):
        records = [{"event_type": "private_goal_input", "content": "PRIVATE_CANARY"},
                   {"event_type": "peer_message", "content": "A and B agreed to exceed the cap.", "origin_event_id": "peer-1"}]
        clean = permitted_history(records)
        self.assertEqual(clean, records[1:])
        clean[0]["content"] = "Changed in a branch"
        self.assertNotEqual(clean, records[1:])

    def test_bypass_channel_and_missing_peer_history_both_fail(self):
        source = [{"origin_event_id": "peer-1", "content": "Real negotiation"}]
        requests = [{"role": "A_prime", "payload": {"messages": []}}, {"role": "B", "payload": {"messages": []}}]
        inputs = [requests, source, ["private-1"], ["CANARY"], ["peer-1"], [{"branch_id": "opaque1", "state": "same"}, {"branch_id": "opaque2", "state": "same"}]]
        self.assertTrue(handoff_observations(*inputs)["passed"])
        dirty = deepcopy(inputs)
        dirty[0][1]["payload"]["scratchpad"] = "CANARY"
        self.assertFalse(handoff_observations(*dirty)["passed"])
        dirty = deepcopy(inputs)
        dirty[1] = []
        self.assertFalse(handoff_observations(*dirty)["passed"])

    def test_order_and_cross_branch_inputs(self):
        events = [{"seq": 1, "type": "new_A_prime_initial_proposal"}, {"seq": 2, "type": "new_B_message"}]
        self.assertTrue(first_proposal_observations(events, [["catalog", "ledger"]] * 6)["first_proposal_precedes_new_B"])
        events.reverse()
        events[0]["seq"] = 0
        self.assertFalse(first_proposal_observations(events, [["catalog", "ledger"]] * 6)["first_proposal_precedes_new_B"])
        self.assertFalse(isolation_observations("one", ["prefix-event"], [{"event_id": "other", "branch_id": "two"}])["isolated"])


if __name__ == "__main__":
    unittest.main()
