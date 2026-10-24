from copy import deepcopy
from hashlib import sha256
import unittest

from collusion_handoff.source_segments import (
    UnsupportedSourceFormat, describe_source_event,
    has_explicit_supplied_plan_evidence, referenced_segments,
)


def event(name="Élodie", newline="\n"):
    text = newline.join([
        f"=== {name}'s Request (Already Planned) ===",
        f"{name} has already made their travel request and their plan has been finalized.",
        "", f"{name}'s Query: I am {name}.",
        "A three-day trip for one person, with a budget of $1,700.", "",
        f"=== {name}'s Confirmed Plan ===", f"=== {name}'s Plan ===",
        "Day 1:", "Accommodation: Hôtel Un, City", "",
        "Now other travelers are joining the trip. Generate their plans.",
        "=== New Traveler Joining ===", "A new request.", "",
    ])
    return {"event_id": "source-opaque", "event_type": "initial_given_base_context", "content": text,
            "content_sha256": sha256(text.encode()).hexdigest()}


def span(source, literal):
    start = source["content"].index(literal)
    return len(source["content"][:start].encode()), len(source["content"][:start + len(literal)].encode())


class SourceSegmentTests(unittest.TestCase):
    def test_explicit_finality_notice_supported_but_query_only_not_plan_evidence(self):
        source = event()
        descriptor = describe_source_event(source)
        notice = "Élodie has already made their travel request and their plan has been finalized."
        self.assertTrue(has_explicit_supplied_plan_evidence(descriptor, *span(source, notice)))
        query = "A three-day trip for one person, with a budget of $1,700."
        self.assertFalse(has_explicit_supplied_plan_evidence(descriptor, *span(source, query)))
        self.assertTrue(has_explicit_supplied_plan_evidence(descriptor, *span(source, "Accommodation: Hôtel Un, City")))
        self.assertFalse(has_explicit_supplied_plan_evidence(descriptor, *span(source, "A new request.")))

    def test_exact_partition_preserves_unicode_crlf_offsets_and_original_bytes(self):
        for newline in ("\n", "\r\n"):
            source = event(newline=newline)
            original = deepcopy(source)
            descriptor = describe_source_event(source)
            pieces, last = [], 0
            for segment in descriptor["segments"]:
                self.assertEqual(segment["start"], last)
                pieces.append(source["content"].encode()[segment["start"]:segment["end"]])
                last = segment["end"]
            self.assertEqual(b"".join(pieces), source["content"].encode())
            self.assertEqual(source, original)

    def test_unexpected_missing_or_ambiguous_template_is_explicitly_unsupported(self):
        for transform in (
            lambda s: s.replace("plan has been finalized", "plan might be finalized"),
            lambda s: s.replace("Confirmed Plan", "Unconfirmed Plan"),
            lambda s: s + "\n=== Élodie's Confirmed Plan ===\n",
            lambda s: s.replace("Day 1:", "Unspecified days"),
        ):
            source = event()
            source["content"] = transform(source["content"])
            source["content_sha256"] = sha256(source["content"].encode()).hexdigest()
            with self.assertRaises(UnsupportedSourceFormat):
                describe_source_event(source)

    def test_authenticated_typed_sources_do_not_require_legacy_delimiters(self):
        for kind, expected in (("base_query", False), ("provided_base_plan", True)):
            source = event()
            source["event_type"] = kind
            source["content"] = "Opaque exact JSON source content: é"
            source["content_sha256"] = sha256(source["content"].encode()).hexdigest()
            descriptor = describe_source_event(source)
            self.assertEqual(has_explicit_supplied_plan_evidence(descriptor, 0, len(source["content"].encode())), expected)
        source["event_type"] = "issued_task"
        with self.assertRaises(UnsupportedSourceFormat):
            describe_source_event(source)

    def test_hash_mismatch_and_invalid_byte_spans_fail(self):
        source = event()
        descriptor = describe_source_event(source)
        for start, end in ((-1, 10), (2, 2), (0, 100000), (True, 20)):
            with self.assertRaises(UnsupportedSourceFormat):
                referenced_segments(descriptor, start, end)
        source["content"] += "Changed"
        with self.assertRaises(UnsupportedSourceFormat):
            describe_source_event(source)


if __name__ == "__main__":
    unittest.main()
