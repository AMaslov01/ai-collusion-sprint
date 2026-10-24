"""Real native code with fixture model transport; no inference or paid routes."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from collusion_handoff.artifacts import read_json
from collusion_handoff.checkpoint import load_checkpoint
from collusion_handoff.native_adapter import install_data, run_first_group
from collusion_handoff.native_replay import load_verified_result


@unittest.skipUnless(Path("/tasks/data.jsonl").exists(), "Native integration requires the pinned-data mount")
class NativeCollectionTests(unittest.TestCase):
    def test_checkpoint_replay_and_verified_result_reuse_without_generation(self):
        config = read_json(Path("/code/configs/dev-package.draft.json"))
        module = install_data(config)
        group = next(row for row in module.load_travel_data() if row["id"] == 1)
        queries = group["questions"][:4]
        emitted = []

        def fixture_model(request):
            body = json.loads(request.read())
            index = len(emitted)
            emitted.append(body)
            number = max(0, index-1)
            for future in queries[number+1:]:
                self.assertNotIn(future["query"], json.dumps(body["messages"]))
            if index == 0:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "offline-tool-1", "type": "function", "function": {
                        "name": "AccommodationSearch", "arguments": '{"city":"Rockford"}',
                    },
                }]}
                finish = "tool_calls"
            else:
                name = queries[index-1]["name"]
                content = f"=== {name}'s Plan ===\nDay 1:\nCurrent City: -\nTransportation: -\nBreakfast: -\nAttraction: -\nLunch: -\nDinner: -\nAccommodation: -\n"
                message = {"role": "assistant", "content": content}
                finish = "stop"
            return httpx.Response(200, json={
                "id": f"fixture-{index}", "object": "chat.completion", "created": 0,
                "model": config["model"]["served_alias"],
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            })

        def fixture_tokenizer(request):
            self.assertEqual(request.url.path, "/tokenize")
            return httpx.Response(200, json={"count": 100, "max_model_len": 32768})

        with tempfile.TemporaryDirectory() as directory, patch("collusion_handoff.native_adapter.WORK", Path(directory)):
            root = Path(directory)
            run_first_group(config, time.time()+4000, model_transport=httpx.MockTransport(fixture_model),
                            tokenizer_transport=httpx.MockTransport(fixture_tokenizer))
            self.assertEqual(len(emitted), 5)
            self.assertEqual(read_json(root / "audit.json")["model_calls_during_audit"], 0)
            self.assertEqual(len(read_json(root / "audit.json")["verified_jobs"]), 4)
            coverage = read_json(root / "coverage.json")
            self.assertEqual(sum(row["status"] == "verified" for row in coverage["jobs"]), 4)
            self.assertEqual(sum(row["status"] == "not_run" for row in coverage["jobs"]), 44)
            scientific = read_json(root / "manifest.json")["scientific_fingerprint"]
            manifest, snapshot = load_checkpoint(root / "checkpoints/group-1/traveler-2", scientific)
            self.assertEqual(manifest["next_round_idx"], 3)
            self.assertEqual(len(snapshot["actor"]["fields"]["all_queries"]), 3)  # base + two issued travelers
            result = load_verified_result(root, Path("results/group-1-traveler-1.json"), scientific)
            self.assertTrue(result["parse_valid"])
            self.assertFalse(result["native_approximate_secondary_reward"])
            self.assertEqual(len(emitted), 5)  # verified result reuse has no model transport
            with self.assertRaises(ValueError):
                load_verified_result(root, Path("results/group-1-traveler-1.json"), "different-fingerprint")
            actor_file = root / "checkpoints/group-1/traveler-2/actor.json"
            actor_file.write_text(actor_file.read_text() + " ")
            with self.assertRaises(ValueError):
                load_checkpoint(actor_file.parent, scientific)
