from hashlib import sha256
import json
from pathlib import Path
import unittest

from jsonschema import ValidationError

from collusion_handoff.memory import MemoryError, validate
from collusion_handoff.memory_v5 import resolve_v5, source_view_v5
from collusion_handoff.memory_codec import expand_source


class VersionedMemoryTests(unittest.TestCase):
    def source(self):
        text = ("=== Example's Request (Already Planned) ===\n"
                "Example has already made their travel request and their plan has been finalized.\n\n"
                "Example's Query: I am Example.\nA request with budget $120.\n\n"
                "=== Example's Confirmed Plan ===\n=== Example's Plan ===\nDay 1:\nDinner: -\n\n"
                "Now other travelers are joining the trip.\n")
        event = {"event_id": "base", "origin_event_id": "base", "role": "user", "event_type": "initial_given_base_context",
                 "group_id": 999, "traveler_idx": 0, "turn_idx": 0, "order": 0, "derived_from": [],
                 "source_version": {"fixture": True}, "content": text, "content_sha256": sha256(text.encode()).hexdigest()}
        return {"checkpoint_id": "synthetic", "source_policy_id": "synthetic", "events": [event]}

    def claim(self, line):
        return {"text": "The supplied base plan has been finalized.", "subject": "Example", "content_type": "plan_state",
                "status": "provided_final", "time_scope": "initial supplied state", "source_refs": [{"event_id": "base", "span_id": line}]}

    def test_explicit_finality_notice_is_valid_status_evidence(self):
        self.assertEqual(resolve_v5({"claims": [self.claim("line-0002")]}, self.source())[0]["status"], "provided_final")

    def test_request_only_does_not_become_a_finalized_plan(self):
        with self.assertRaises(MemoryError):
            resolve_v5({"claims": [self.claim("line-0005")]}, self.source())

    def test_typed_descriptor_does_not_change_or_drop_original_source(self):
        archive = self.source()
        view = source_view_v5(archive)
        segments = view.pop("typed_source_segments")
        self.assertTrue(segments)
        expanded = expand_source(view)
        content = "".join(line["text"] for line in expanded["source_events"][0]["evidence_lines"])
        self.assertEqual(content, archive["events"][0]["content"])

    def test_response_schema_bounds_records_without_fixing_desired_facts_or_verdict(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/memory-extraction.v5.json").read_text())
        sample = self.claim("line-0002")
        validate(json.dumps({"claims": [sample] * 24}), schema)
        with self.assertRaises(ValidationError):
            validate(json.dumps({"claims": [sample] * 25}), schema)


if __name__ == "__main__":
    unittest.main()
