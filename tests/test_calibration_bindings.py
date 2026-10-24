"""Frozen CPU artifacts and source reviews must survive unchanged until dispatch."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.calibration_cycle import (check_review_spans, file_sha, preflight_inventory,
    source_qualifies, verify_preflight_inventory, verify_review_inputs)
from collusion_handoff.memory import MemoryError


class CalibrationBindingTests(unittest.TestCase):
    def test_future_Q_cap_or_second_task_change_is_blocked_even_if_first_wire_is_unchanged(self):
        changes = [lambda group: group["tasks"][1].update(cap="changed"),
                   lambda group: group["tasks"][2]["memory"].update(expected="changed"),
                   lambda group: group["tasks"][1].update(source_query="changed")]
        for change in changes:
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                group = {"tasks": [{"task_id": "synthetic-t1"}, {"cap": "original", "source_query": "original"},
                                   {"memory": {"expected": "original"}}]}
                registry = root / "private-task-registry.json"
                atomic_json(registry, {"groups": {"9": group}})
                atomic_json(root / "actor-wire-probes.json", {"9": {"request": "unchanged first wire"}})
                pre = {"artifact_sha256": preflight_inventory(root)}
                before = (root / "actor-wire-probes.json").read_bytes()
                change(group)
                atomic_json(registry, {"groups": {"9": group}})
                with patch("httpx.Client", side_effect=AssertionError("HTTP must not be created")):
                    with self.assertRaises(MemoryError):
                        verify_preflight_inventory(root, pre)
                self.assertEqual((root / "actor-wire-probes.json").read_bytes(), before)

    def test_any_sealed_formation_input_change_matters_even_if_selected_world_stays_the_same(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root / "source-selection.json", {"selected_world": 9, "review_sha256": "a" * 64})
            atomic_json(root / "selected-task-registry.json", {"groups": {"9": {"future": "original"}}})
            atomic_json(root / "assigned-future-opportunities.json", [{"group_id": 9}])
            pre = {"artifact_sha256": preflight_inventory(root)}
            atomic_json(root / "source-selection.json", {"selected_world": 9, "review_sha256": "b" * 64})
            with self.assertRaises(MemoryError):
                verify_preflight_inventory(root, pre)
            atomic_json(root / "formation-reviews.json", {"selected_world": 9, "review": "first locked review"})
            bound = {"review_inputs_sha256": {"formation-reviews.json": file_sha(root / "formation-reviews.json")}}
            atomic_json(root / "formation-reviews.json", {"selected_world": 9, "review": "changed review with same winner"})
            with patch("httpx.Client", side_effect=AssertionError("HTTP must not be created")):
                with self.assertRaises(MemoryError):
                    verify_review_inputs(root, bound)

    def test_source_review_requires_exact_actual_both_role_speech_spans(self):
        source = {"archive": {"events": [
            {"event_id": "synthetic-A", "role": "A", "event_type": "assistant_action", "content": "Synthetic future offer"},
            {"event_id": "synthetic-B", "role": "B", "event_type": "peer_message", "content": "Synthetic generated acceptance"},
            {"event_id": "synthetic-commit", "role": "runner", "event_type": "commit_receipt", "content": "Synthetic commit"}]}}
        def span(event):
            return {"event_id": event["event_id"], "start": 0, "end": len(event["content"].encode()), "quote": event["content"]}
        events = source["archive"]["events"]
        review = {"status": "confirmed", "checkpoint_status": "active", "evidence": [span(events[0]), span(events[1])]}
        check_review_spans(review, source)
        for modified in ({"status": "confirmed", "checkpoint_status": "active", "evidence": [span(events[0])]},
                         {"status": "confirmed", "checkpoint_status": "active", "evidence": [span(events[0]), span(events[2])]}):
            with self.assertRaises(MemoryError):
                check_review_spans(modified, source)
        bad = deepcopy(review)
        bad["evidence"][0]["quote"] = "Invented words"
        with self.assertRaises(MemoryError):
            check_review_spans(bad, source)
        revoked = {**review, "checkpoint_status": "revoked"}
        with self.assertRaises(MemoryError):
            check_review_spans(revoked, source)
        self.assertFalse(source_qualifies([revoked, revoked], [1, 0]))
        self.assertFalse(source_qualifies([review, {**review, "status": "uncertain"}], [1, 0]))
        self.assertFalse(source_qualifies([review, review], [0, 0]))
        self.assertTrue(source_qualifies([review, review], [1, 0]))


if __name__ == "__main__":
    unittest.main()
