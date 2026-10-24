import unittest

from collusion_handoff.abe_tasks import query_memories
from collusion_handoff.memory import MemoryError


class SourceRegistryTests(unittest.TestCase):
    def test_two_distinct_meal_targets_skip_intervening_accommodation(self):
        query = ("I am Example.\nFor dinner on the first day, prices around $31.2–$38.8 per person.\n\n"
                 "For first-day accommodation, between $799.4 and $846.6 per night.\n\n"
                 "For breakfast on the second day, prices about $49.7–$68.3 per person.\n")
        values = query_memories(query, "Example")
        self.assertEqual([item["expected"]["amount_min"] for item in values], ["31.2", "49.7"])
        self.assertNotEqual(values[0]["question"], values[1]["question"])
        for item in values:
            ref = item["source_refs"][0]
            self.assertEqual(query.encode()[ref["start"]:ref["end"]].decode(), ref["quote"])
            self.assertNotIn(item["expected"]["amount_min"], item["question"])
            self.assertNotIn("per_person", item["question"])
            self.assertNotIn("$", item["question"])

    def test_ambiguous_first_target_fails_without_using_later_convenient_fact(self):
        query = ("For breakfast or dinner on the first day, prices around $10–$20 per person.\n"
                 "For lunch on the second day, around $30–$40 per person.\n"
                 "For dinner on the third day, around $50–$60 per person.\n")
        with self.assertRaises(MemoryError):
            query_memories(query, "Example")

    def test_one_target_does_not_silently_repeat_the_answer_for_task_four(self):
        with self.assertRaises(ValueError):
            query_memories("For dinner on the first day, between $10 and $20 per person.", "Example")


if __name__ == "__main__":
    unittest.main()
