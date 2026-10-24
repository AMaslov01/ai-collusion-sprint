from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from jsonschema import ValidationError

from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.cycle_budget import CycleBudget
from collusion_handoff.memory import MemoryError, validate
from collusion_handoff.memory_v3 import derive_verification, fixture_source, resolve_v3
from collusion_handoff.native_data import _convert_row
from collusion_handoff import repair_cycle


CODE = Path(__file__).resolve().parents[1]
DEFINITIONS = read_json(CODE / "fixtures/memory-semantic-v3.json")
V_SCHEMA = read_json(CODE / "schemas/memory-verification.v3.json")


class FacetTests(unittest.TestCase):
    def row(self):
        return {"claim_id": "x", "actor_facets": {name: {"verdict": "supported", "reason": "Evidence checked."}
                for name in ("content", "attribution", "status_time", "conditions_units")},
                "research_metadata": {"verdict": "supported", "reason": "Source metadata checked."}}

    def test_every_compound_facet_can_prevent_false_accept(self):
        for name in ("content", "attribution", "status_time", "conditions_units"):
            row = self.row()
            row["actor_facets"][name]["verdict"] = "insufficient_evidence"
            result = derive_verification({"verdicts": [row]})["verdicts"][0]
            self.assertEqual(result["verdict"], "insufficient_evidence")
            self.assertEqual(result["research_metadata_verdict"], "supported")

    def test_research_defect_is_separate_from_actor_text(self):
        row = self.row()
        row["research_metadata"]["verdict"] = "contradicted"
        result = derive_verification({"verdicts": [row]})["verdicts"][0]
        self.assertEqual(result["actor_text_verdict"], "supported")
        self.assertEqual(result["verdict"], "contradicted")

    def test_NA_cannot_bypass_material_units_or_status(self):
        for name in ("content", "attribution", "status_time", "conditions_units"):
            row = self.row()
            row["actor_facets"][name]["verdict"] = "not_applicable"
            with self.assertRaises(ValidationError):
                validate(json.dumps({"verdicts": [row]}), V_SCHEMA)

    def test_fixture_negative_committed_reaches_V_but_native_guard_rejects_it(self):
        definition = next(item for item in DEFINITIONS["fixtures"] if item["fixture_id"].startswith("F06"))
        source, claims = fixture_source(definition)
        negative = next(item for item in claims if item["claim_id"].endswith("committed_from_issued"))
        self.assertEqual(negative["status"], "committed")
        self.assertEqual(negative["source_roles"][0]["role"], "A")
        event = source["archive"]["events"][0]
        raw = {"claims": [{key: value for key, value in negative.items() if key in {"text", "subject", "status", "time_scope", "content_type"}}]}
        raw["claims"][0]["source_refs"] = [{"event_id": event["event_id"], "quote": event["content"]}]
        with self.assertRaises(MemoryError):
            resolve_v3(raw, source["archive"])

    def test_fixture_roles_and_ids_are_source_derived_and_complete(self):
        cases, eligible = [], []
        for definition in DEFINITIONS["fixtures"]:
            source, claims = fixture_source(definition)
            cases.extend(claim["claim_id"] for claim in claims)
            eligible.extend(definition["P_eligible"])
            by_id = {event["event_id"]: event for event in source["archive"]["events"]}
            for claim in claims:
                for role in claim["source_roles"]:
                    self.assertEqual(role["role"], by_id[role["event_id"]]["role"])
        self.assertEqual(len(cases), 17)
        self.assertEqual(len(set(cases)), 17)
        self.assertEqual(len(eligible), 9)


class GlobalBudgetTests(unittest.TestCase):
    def config(self):
        return {"limits": {"absolute_cutoff_utc": "2026-10-24T15:00:00Z", "gpu_service_wall_seconds": 7200,
                           "max_new_model_attempts": 2, "max_input_output_tokens": 10000}, "allocations": {"test": 1}}

    def payload(self):
        return {"model": "qwen-native-dev", "messages": [{"role": "user", "content": "Fixture"}], "max_tokens": 100}

    def test_allocation_and_duplicate_limits_survive_new_budget_instance(self):
        dispatched = []
        def handler(request):
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"count": 10, "max_model_len": 32768})
            dispatched.append(request)
            return httpx.Response(200, json={"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        with tempfile.TemporaryDirectory() as temp, httpx.Client(transport=httpx.MockTransport(handler)) as http:
            one = CycleBudget(temp, self.config(), http, phase_work=temp)
            one.dispatch(self.payload(), "first", "test")
            two = CycleBudget(temp, self.config(), http, phase_work=temp)
            self.assertEqual(two.state()["accounted_input_output_tokens"], 15)
            for identity in ("first", "second"):
                with self.assertRaises(MemoryError):
                    two.dispatch(self.payload(), identity, "test")
        self.assertEqual(len(dispatched), 1)

    def test_unknown_spend_reserves_full_budget_and_never_retries(self):
        def handler(request):
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"count": 10, "max_model_len": 32768})
            raise httpx.ReadTimeout("Injected transport loss", request=request)
        with tempfile.TemporaryDirectory() as temp, httpx.Client(transport=httpx.MockTransport(handler)) as http:
            budget = CycleBudget(temp, self.config(), http, phase_work=temp)
            with self.assertRaises(httpx.ReadTimeout):
                budget.dispatch(self.payload(), "first", "test")
            next_instance = CycleBudget(temp, self.config(), http, phase_work=temp)
            self.assertEqual(next_instance.state()["accounted_input_output_tokens"], 110)
            self.assertEqual(next_instance.state()["unknown_spend_requests"], ["first"])
            with self.assertRaises(MemoryError):
                next_instance.check("first", "test")

    def test_truncated_ledger_blocks_inference_instead_of_resetting(self):
        with tempfile.TemporaryDirectory() as temp, httpx.Client(transport=httpx.MockTransport(lambda request: None)) as http:
            budget = CycleBudget(temp, self.config(), http, phase_work=temp)
            (Path(temp) / "ledger/requests.jsonl").write_text('{"logical_call_id":')
            with self.assertRaises(MemoryError):
                budget.state()


@unittest.skipUnless(Path("/tasks/data.jsonl").exists(), "Requires pinned native-data mount")
class NativeV3FixtureTests(unittest.TestCase):
    def test_actual_group2_two_travelers_replay_and_source_without_inference(self):
        config = read_json(CODE / "configs/dev-package.draft.json")
        row = next(_convert_row(json.loads(line)) for line in Path("/tasks/data.jsonl").read_text().splitlines() if json.loads(line)["id"] == 2)
        names = [item["name"] for item in row["questions"][:2]]

        class FixtureBudget:
            http = None
            def deadline(self): return time.time() + 3600
            def check(self, *args, **kwargs): return {}
            def preview(self, payload, **kwargs): return 100
            def dispatch(self, payload, logical_id, allocation, **kwargs):
                number = int(logical_id.split("/")[2].split("-")[1])
                text = "\n".join(f"=== {name}'s Plan ===\nDay 1:\nCurrent City: -\nTransportation: -\nBreakfast: -\nAttraction: -\nLunch: -\nDinner: -\nAccommodation: -\n" for name in names[:number])
                return httpx.Response(200, json={"id": logical_id, "object": "chat.completion", "created": 0, "model": "qwen-native-dev",
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})

        with tempfile.TemporaryDirectory() as temp, patch.object(repair_cycle, "WORK", Path(temp)), patch.dict("os.environ", {"ADAPTER_COMMIT": "f" * 40}):
            atomic_json(Path(temp) / "resolved-engine.json", {"fixture_transport": True})
            job = repair_cycle.native_group(2, config, FixtureBudget())
            repair_cycle.replay_native(job, 2, config)
            source = repair_cycle.build_source(job, repair_cycle.UPSTREAM, group_id=2)
            self.assertEqual(source["prefix_model_attempts"], 2)
            self.assertEqual({event["group_id"] for event in source["archive"]["events"]}, {2})
            self.assertFalse((job / "results/group-2-traveler-3.json").exists())
            self.assertEqual(read_json(job / "audit.json")["new_model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
