import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

from collusion_handoff.abe_cycle import hardware_smoke
from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.memory import MemoryError


class HardwareCycleTests(unittest.TestCase):
    def test_actual_preflight_wire_binding_rejects_changed_first_body_before_budget(self):
        from collusion_handoff import readiness_cycle as cycle
        root = Path(__file__).resolve().parents[1]
        config = read_json(root / "configs/readiness-cycle-v5.json")
        base = read_json(root / "configs/dev-package.draft.json")
        logical_id = config["resource_smoke"]["logical_prefix"] + "/hardware-tool"
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            def actor_probe(*args):
                atomic_json(work / "actor-preflight.json", {"status": "synthetic_no_calls"})
            with patch.object(cycle, "ROOT", work), patch.object(cycle, "WORK", work), \
                    patch.object(cycle, "settings", return_value=(config, base, {})), \
                    patch.object(cycle, "verify_final_DEV_freeze"), patch("collusion_handoff.abe_cycle.actor_preflight", side_effect=actor_probe), \
                    patch.dict("os.environ", {"CYCLE_PHASE": "validation-prefix-v1", "ADAPTER_COMMIT": "a" * 40}):
                cycle.preflight()
                probes = read_json(work / "wire-probes.json")
                self.assertEqual(list(probes), ["hardware-smoke-l40s-r2/hardware-tool"])
                body = probes["hardware-smoke-l40s-r2/hardware-tool"]
                changed = {**body, "seed": body["seed"] + 1}
                budget = Mock()
                with patch.object(cycle, "read_json", return_value=probes):
                    with self.assertRaises(MemoryError):
                        cycle.dispatch(budget, changed, logical_id, "reviewed_targeted_repairs", work)
                    budget.dispatch.assert_not_called()
                    cycle.dispatch(budget, body, logical_id, "reviewed_targeted_repairs", work)
                    budget.dispatch.assert_called_once_with(body, logical_id, "reviewed_targeted_repairs", job=work)
                budget.reset_mock()
                with patch.object(cycle, "read_json", return_value={}):
                    with self.assertRaises(MemoryError):
                        cycle.dispatch(budget, body, logical_id, "reviewed_targeted_repairs", work)
                    budget.dispatch.assert_not_called()

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
