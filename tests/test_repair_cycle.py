from copy import deepcopy
from hashlib import sha256
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
from collusion_handoff.memory_v3 import (derive_verification, fixture_source, resolve_v3, evidence_lines,
                                         evidence_view, resolve_v31, fixture_mapping, fixture_eligible, messages_v31)
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
        negative = next(item for item in claims if item["extraction_index"] == 1)
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

    def test_all_model_visible_fixture_identifiers_are_opaque(self):
        asset = {"prompt": "Verify only the source.", "schema": V_SCHEMA}
        for definition in DEFINITIONS["fixtures"]:
            source, claims = fixture_source(definition)
            data = json.loads(messages_v31("V", source, claims, asset)[1]["content"])
            self.assertRegex(data["checkpoint_id"], r"^fixture-[0-9]{2}$")
            for event in data["source_events"]:
                self.assertRegex(event["event_id"], r"^fixture-[0-9]{2}/event-[0-9]{2}$")
                self.assertNotIn("audit_alias", event)
            for claim in data["candidates"]:
                self.assertRegex(claim["claim_id"], r"^fixture-[0-9]{2}/claim-[0-9]{2}$")
                self.assertNotIn("alias", claim)
                self.assertNotIn("P_eligible", claim)
            mapping = fixture_mapping(definition, source, claims)
            self.assertEqual(set(mapping["aliases_to_model_claim_ids"]), {item["alias"] for item in definition["candidates"]})
            self.assertEqual(len(fixture_eligible(definition, claims)), len(definition["P_eligible"]))


class EvidenceLineTests(unittest.TestCase):
    def source(self):
        content = "é\r\nsame\nsame\n"
        event = {"event_id": "event", "origin_event_id": "event", "role": "A", "event_type": "final_plans",
                 "group_id": 1, "traveler_idx": 1, "turn_idx": 1, "order": 0, "derived_from": [],
                 "source_version": {"version": "fixture"}, "content": content, "content_sha256": sha256(content.encode()).hexdigest()}
        return {"checkpoint_id": "test", "source_policy_id": "test", "events": [event]}

    def claim(self, span):
        return {"text": "A issued a plan.", "content_type": "plan_state", "subject": "A", "time_scope": "past", "status": "issued_plan",
                "source_refs": [{"event_id": "event", "span_id": span}]}

    def test_UTF8_CRLF_and_repeated_line_addresses_roundtrip_exactly(self):
        archive = self.source()
        event = archive["events"][0]
        view = evidence_view(archive)
        self.assertEqual("".join(line["text"] for line in view["source_events"][0]["evidence_lines"]), event["content"])
        self.assertNotIn("content", view["source_events"][0])
        claims = resolve_v31({"claims": [self.claim("line-0002"), self.claim("line-0003")]}, archive)
        self.assertEqual([(item["source_refs"][0]["start"], item["source_refs"][0]["end"]) for item in claims], [(4, 9), (9, 14)])
        self.assertEqual([item["source_evidence"][0]["span_ids"] for item in claims], [["line-0002"], ["line-0003"]])

    def test_unknown_span_or_changed_source_is_whole_C_failure(self):
        archive = self.source()
        with self.assertRaises(MemoryError):
            resolve_v31({"claims": [self.claim("line-9999")]}, archive)
        archive["events"][0]["content"] += "x"
        with self.assertRaises(MemoryError):
            evidence_view(archive)

    def test_contiguous_context_range_has_exact_original_bytes(self):
        archive = self.source()
        claim = self.claim("line-0002")
        claim["source_refs"][0]["end_span_id"] = "line-0003"
        resolved = resolve_v31({"claims": [claim]}, archive)[0]
        self.assertEqual((resolved["source_refs"][0]["start"], resolved["source_refs"][0]["end"]), (4, 14))
        self.assertEqual(resolved["source_evidence"][0]["span_ids"], ["line-0002", "line-0003"])
        claim["source_refs"][0] = {"event_id": "event", "span_id": "line-0003", "end_span_id": "line-0001"}
        with self.assertRaises(MemoryError):
            resolve_v31({"claims": [claim]}, archive)


class IndependentFixtureComponentTests(unittest.TestCase):
    def test_P_still_runs_on_reference_eligible_input_when_V_is_malformed(self):
        definition = next(item for item in DEFINITIONS["fixtures"] if item["fixture_id"].startswith("F02"))
        contract = read_json(CODE / "configs/memory-group1.json")
        assets = {"V": {"prompt": "Verify.", "schema": V_SCHEMA},
                  "P": {"prompt": "Apply policy.", "schema": read_json(CODE / "schemas/memory-policy.proposed.json")}}
        calls = []
        class FixtureBudget:
            def dispatch(self, payload, logical_id, allocation, **kwargs):
                stage = logical_id.split("/")[-1]
                calls.append(stage)
                candidates = json.loads(payload["messages"][1]["content"])["candidates"]
                text = "not JSON" if stage == "V" else json.dumps({"verdicts": [{"claim_id": item["claim_id"], "label": "exclude_unauthorized_commitment", "reason": "Ongoing harmful obligation."} for item in candidates]})
                return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": text}}],
                                               "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
        with tempfile.TemporaryDirectory() as temp, patch.object(repair_cycle, "WORK", Path(temp)):
            repair_cycle.fixture_job(definition, contract, assets, FixtureBudget())
            status = read_json(Path(temp) / "fixtures" / definition["fixture_id"] / "status.json")
            self.assertEqual(calls, ["V", "P"])
            self.assertEqual(status["status"], "partial_component_failure")
            self.assertIsNone(status["actual_v_supported_ids"])
            self.assertIsNone(status["e2e_retained_reference_eligible_ids"])
            self.assertEqual(status["component_p_allowed_ids"], [])


class GlobalBudgetTests(unittest.TestCase):
    def config(self):
        cutoff = datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat()
        return {"limits": {"absolute_cutoff_utc": cutoff, "gpu_service_wall_seconds": 7200,
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
