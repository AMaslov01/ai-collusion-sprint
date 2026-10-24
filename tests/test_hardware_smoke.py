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
        self.check_probe("hardware_smoke", "hardware-and-development-prefix-v1", "hardware-smoke", {})

    def test_new_hardware_probe_uses_reserve_and_unique_logical_ids(self):
        self.check_probe("reviewed_targeted_repairs", "validation-prefix-v1/hardware-smoke-l40s-r2", "hardware-smoke-l40s-r2",
                         {"allocation": "reviewed_targeted_repairs", "logical_prefix": "validation-prefix-v1/hardware-smoke-l40s-r2", "job_name": "hardware-smoke-l40s-r2"})

    def check_probe(self, expected_allocation, logical_prefix, job_name, options):
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
        identities = []
        def dispatch(budget, body, logical_id, allocation, job):
            self.assertEqual(allocation, expected_allocation)
            self.assertEqual(job.name, job_name)
            identities.append(logical_id)
            self.assertEqual(body["temperature"], 0.7)
            self.assertNotIn("UNSET", json.dumps(body))
            sent.append(body)
            return httpx.Response(200, request=httpx.Request("POST", "http://offline-fixture/v1/chat/completions"),
                                  json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(actions[len(sent)-1])}}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
        with tempfile.TemporaryDirectory() as directory, patch("collusion_handoff.abe_cycle.load_accommodations", return_value=catalog), \
                patch("collusion_handoff.readiness_cycle.dispatch", side_effect=dispatch):
            hardware_smoke(config, base, None, Path(directory), **options)
            status = read_json(Path(directory) / job_name / "status.json")
            self.assertEqual(status["status"], "passed_typed_tool_cycle")
            self.assertFalse(status["commit_created"])
            self.assertEqual(len(sent), 2)
            self.assertEqual(identities, [logical_prefix + "/hardware-tool", logical_prefix + "/hardware-proposal"])
            self.assertEqual(status["allocation"], expected_allocation)
            receipt = read_json(Path(directory) / job_name / "tool-receipt.json")
            self.assertEqual(receipt["result"]["items"], list(catalog.values()))
            self.assertIn("stable-id", json.dumps(sent[1]["messages"]))


if __name__ == "__main__":
    unittest.main()
