import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from collusion_handoff.abe_cycle import hardware_smoke
from collusion_handoff.artifacts import read_json


class HardwareCycleTests(unittest.TestCase):
    def test_two_real_typed_actions_use_the_actual_canonical_tool_result(self):
        root = Path(__file__).resolve().parents[1]
        config = read_json(root / "configs/readiness-cycle-v5.json")
        base = read_json(root / "configs/dev-package.draft.json")
        catalog = {"stable-id": {"catalog_item_id": "stable-id", "city": "Rockford", "row_index": 2, "price": "91"}}
        actions = [{"type": "tool_request", "tool": "catalog_search", "arguments": {"city": "Rockford", "minimum_price": None, "maximum_price": None, "offset": 0}},
                   {"type": "proposal", "task_id": "hardware-probe-task", "note": "Use the listed item.",
                    "plan": {"traveler_id": "hardware-probe-person", "slots": [{"slot_id": "lodging_1", "catalog_item_id": "stable-id", "rooms": 1, "nights": 1,
                              "reference_plan_versions": [], "evidence_refs": ["hardware-tool-receipt"]}],
                              "memory_answer": {"amount_min": None, "amount_max": None, "approximate": None, "basis": None, "currency_marker": None}}}]
        sent = []
        def dispatch(budget, body, logical_id, allocation, job):
            self.assertEqual(allocation, "hardware_smoke")
            self.assertEqual(body["temperature"], 0.7)
            self.assertNotIn("UNSET", json.dumps(body))
            sent.append(body)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(actions[len(sent)-1])}}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
        with tempfile.TemporaryDirectory() as directory, patch("collusion_handoff.abe_cycle.load_accommodations", return_value=catalog), \
                patch("collusion_handoff.readiness_cycle.dispatch", side_effect=dispatch):
            hardware_smoke(config, base, None, Path(directory))
            status = read_json(Path(directory) / "hardware-smoke/status.json")
            self.assertEqual(status["status"], "passed_typed_tool_cycle")
            self.assertFalse(status["commit_created"])
            self.assertEqual(len(sent), 2)
            receipt = read_json(Path(directory) / "hardware-smoke/tool-receipt.json")
            self.assertEqual(receipt["result"]["items"], list(catalog.values()))
            self.assertIn("stable-id", json.dumps(sent[1]["messages"]))


if __name__ == "__main__":
    unittest.main()
