"""Synthetic rejection tests for semantic Q matching and immutable source bytes."""

from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/formalize-main-source-review.py"
if not SCRIPT.exists():
    SCRIPT = Path(__file__).with_name("formalize-main-source-review.py")
spec = importlib.util.spec_from_file_location("source_Q_formalizer", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class SourceQFormalizationTests(unittest.TestCase):
    def fixture(self):
        clause = "For lunch on day 2, priced around $10–$20 per person."
        query = "I am Ivy.\n" + clause + "\n"
        body = json.dumps({"current_public_task": {"source_query": query}}, ensure_ascii=False)
        event = {"event_id": "synthetic-source", "event_type": "issued_task", "traveler_idx": 1,
                 "role": "user", "content": body, "content_sha256": sha256(body.encode()).hexdigest()}
        memory = {"question": "Recall Ivy's originally requested day 2 lunch price interval. Return its minimum, maximum, approximation indicator, stated calculation basis, and literal currency marker. Do not infer a national currency.",
                  "expected": {"amount_min": "10", "amount_max": "20", "approximate": True,
                               "basis": "per_person", "currency_marker": "$"},
                  "source_refs": [{"delivered_origin_event_id": event["event_id"], "query_sha256": sha256(query.encode()).hexdigest(),
                                   "start": len("I am Ivy.\n".encode()), "end": len(query.rstrip("\n").encode()), "quote": clause}]}
        meaning = {"traveler": "Ivy", "day": 2, "slot": "lunch", "source_event_id": event["event_id"],
                   "amount_min": "10.0", "amount_max": "20.00", "approximate": True,
                   "basis": "per_person", "currency_marker": "$", "status": "requested"}
        return memory, meaning, {event["event_id"]: event}

    def test_same_numbers_do_not_hide_wrong_subject_day_meal_or_qualifiers(self):
        memory, meaning, events = self.fixture()
        before = deepcopy(meaning)
        result = helper.verify_Q_meaning(memory, [meaning], events)
        self.assertEqual(result["independently_sealed_meaning"], before)
        self.assertEqual(meaning, before)
        for field, value in (("traveler", "Other"), ("day", 3), ("slot", "dinner"),
                             ("approximate", False), ("basis", "per_night"), ("currency_marker", "USD"),
                             ("amount_min", "10.1"), ("amount_max", "NaN"), ("status", "negated")):
            with self.subTest(field=field):
                changed = {**meaning, field: value}
                with self.assertRaises(ValueError):
                    helper.verify_Q_meaning(memory, [changed], events)

    def test_ambiguous_meanings_or_changed_source_bytes_are_not_bound(self):
        memory, meaning, events = self.fixture()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            helper.verify_Q_meaning(memory, [meaning, deepcopy(meaning)], events)
        changed = deepcopy(memory)
        changed["source_refs"][0]["quote"] += " Extra text"
        with self.assertRaisesRegex(ValueError, "exact original source bytes"):
            helper.verify_Q_meaning(changed, [meaning], events)
        event = {"event_id": "synthetic-A", "event_type": "assistant_action", "role": "A",
                 "content": '{"note":"Будущая договорённость"}'}
        event["content_sha256"] = sha256(event["content"].encode()).hexdigest()
        span = {"event_id": event["event_id"], "start": 0, "end": len(event["content"].encode()), "quote": event["content"]}
        self.assertEqual(helper.verify_span(span, {event["event_id"]: event}, generated=True), "A")
        span["end"] -= 1
        with self.assertRaisesRegex(ValueError, "UTF8"):
            helper.verify_span(span, {event["event_id"]: event}, generated=True)


if __name__ == "__main__":
    assert os.getuid() != 0 and Path("/.dockerenv").exists(), "Run diagnostics tests in server CPU Docker only"
    unittest.main()
