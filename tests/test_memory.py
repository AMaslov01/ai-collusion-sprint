import json
from pathlib import Path
import tempfile
import time
import unittest
from hashlib import sha256

import httpx

from collusion_handoff.artifacts import canonical, fingerprint
from collusion_handoff.memory import (MemoryError, actor_record, apply_verdicts, build_packages,
                                     claim_content, pack_claims, resolve_claims, strict_json)
from collusion_handoff.memory_runner import BuilderClient


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "configs/memory-group1.json").read_text())
ASSETS = {stage: {"prompt": (ROOT / spec["prompt"]).read_text(), "schema": json.loads((ROOT / spec["schema"]).read_text())}
          for stage, spec in CONTRACT["stages"].items()}


def fixture(content="é reviewed plan"):
    archive = {"checkpoint_id": "test-cp2", "source_policy_id": "test-policy", "events": [
        {"event_id": "event-1", "order": 0, "content": content, "content_sha256": sha256(content.encode()).hexdigest()}]}
    source = {"archive": archive, "archive_sha256": fingerprint(archive)}
    claim = {"text": "The planner reviewed the plan.", "content_type": "plan_state", "speaker": "A",
             "time_scope": "Completed native prefix", "status": "stated",
             "source_refs": [{"event_id": "event-1", "quote": "reviewed plan"}]}
    return source, claim


class MemoryTransformsTest(unittest.TestCase):
    def test_strict_parser_rejects_duplicates_nonfinite_fences_and_trailing_text(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}', '```json\n{}\n```', '{} trailing'):
            with self.subTest(text=text), self.assertRaises((MemoryError, ValueError)):
                strict_json(text)

    def test_unique_quote_resolves_utf8_byte_offsets_without_normalization(self):
        source, claim = fixture()
        resolved = resolve_claims({"claims": [claim]}, source["archive"])[0]
        ref = resolved["source_refs"][0]
        self.assertEqual((ref["start"], ref["end"]), (3, 16))
        for content, quote in (("aaaa", "aaa"), ("same same", "same"), ("a  b", "a b")):
            changed_source, changed_claim = fixture(content)
            changed_claim["source_refs"][0]["quote"] = quote
            with self.subTest(content=content), self.assertRaises(MemoryError):
                resolve_claims({"claims": [changed_claim]}, changed_source["archive"])

    def test_one_bad_reference_invalidates_entire_C(self):
        source, claim = fixture()
        bad = {**claim, "source_refs": [{"event_id": "unknown", "quote": "reviewed plan"}]}
        with self.assertRaises(MemoryError):
            resolve_claims({"claims": [claim, bad]}, source["archive"])

    def test_budget_is_whole_record_prefix_and_deletion_has_no_backfill(self):
        source, claim = fixture()
        claims = resolve_claims({"claims": [claim, {**claim, "text": "X" * 400}, {**claim, "text": "small"}]}, source["archive"])
        limit = len(claim_content(claims[:1]))
        packed, excluded = pack_claims(claims, len, limit=limit)
        self.assertEqual(packed, claims[:1])
        self.assertEqual(excluded, claims[1:])
        self.assertEqual(set(actor_record(packed[0])), {"text", "source_refs"})
        selected, errors = apply_verdicts(packed, [{"claim_id": packed[0]["claim_id"], "verdict": "error"}], field="verdict", retain="supported")
        self.assertEqual(selected, [])
        self.assertEqual(errors, [packed[0]["claim_id"]])
        self.assertEqual(claim_content(selected), "[]")

    def test_verdicts_join_by_id_and_require_exact_unique_set(self):
        source, claim = fixture()
        claims = resolve_claims({"claims": [claim, {**claim, "text": "A second distinct observation."}]}, source["archive"])
        verdicts = [{"claim_id": claim["claim_id"], "verdict": "supported"} for claim in reversed(claims)]
        selected, _ = apply_verdicts(claims, verdicts, field="verdict", retain="supported")
        self.assertEqual([canonical(actor_record(claim)) for claim in selected], [canonical(actor_record(claim)) for claim in claims])
        for broken in (verdicts[:1], verdicts + verdicts[:1], [{"claim_id": "extra", "verdict": "supported"}]):
            with self.assertRaises(MemoryError):
                apply_verdicts(claims, broken, field="verdict", retain="supported")

    def test_pipeline_failure_stops_dependent_calls_without_repair(self):
        source, claim = fixture()
        claim["source_refs"][0]["quote"] = "absent"
        calls = []

        def generate(stage, messages):
            calls.append(stage)
            return json.dumps({"sentences": ["A prior plan was reviewed."]} if stage == "S" else {"claims": [claim]})

        with self.assertRaises(MemoryError):
            build_packages(source, CONTRACT, ASSETS, generate, len, lambda name, value: None)
        self.assertEqual(calls, ["S", "C"])

    def test_builder_actual_wire_and_four_stage_pipeline_without_network(self):
        source, claim = fixture()
        seen = []

        def handler(request):
            payload = json.loads(request.content)
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"count": 100, "max_model_len": 32768})
            self.assertEqual(request.url.path, "/v1/chat/completions")
            seen.append(payload)
            self.assertNotIn("tools", payload)
            self.assertNotIn("response_format", payload)
            self.assertEqual({key: payload[key] for key in CONTRACT["model"]["generation"]}, CONTRACT["model"]["generation"])
            data = json.loads(payload["messages"][1]["content"])
            stage = data["stage"]
            if stage == "S":
                result = {"sentences": ["A prior plan was reviewed."]}
            elif stage == "C":
                result = {"claims": [claim]}
            else:
                key, value = ("verdict", "supported") if stage == "V" else ("label", "allow")
                result = {"verdicts": [{"claim_id": row["claim_id"], key: value, "reason": "Source-grounded test verdict."} for row in data["candidates"]]}
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                                             "usage": {"prompt_tokens": 100, "completion_tokens": 30, "completion_tokens_details": None}})

        with tempfile.TemporaryDirectory() as directory, httpx.Client(transport=httpx.MockTransport(handler)) as http:
            builder = BuilderClient(CONTRACT, "test-plan", http=http, work=directory, deadline=time.time() + 60)
            outputs = {}
            summary = build_packages(source, CONTRACT, ASSETS, builder, len, outputs.__setitem__)
            self.assertEqual(summary["CVP_count"], 1)
            self.assertEqual(outputs["packages/C.json"]["content"], outputs["packages/CV.json"]["content"])
            self.assertEqual(outputs["packages/C.json"]["content"], outputs["packages/CVP.json"]["content"])
            with self.assertRaises(MemoryError):
                builder("S", [])
        self.assertEqual(len(seen), 4)

    def test_transport_failure_spends_attempt_and_cannot_retry(self):
        def handler(request):
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"count": 100, "max_model_len": 32768})
            raise httpx.ReadTimeout("Injected unresolved attempt", request=request)

        with tempfile.TemporaryDirectory() as directory, httpx.Client(transport=httpx.MockTransport(handler)) as http:
            builder = BuilderClient(CONTRACT, "test-plan", http=http, work=directory, deadline=time.time() + 60)
            with self.assertRaises(httpx.ReadTimeout):
                builder("S", [])
            with self.assertRaises(MemoryError):
                builder("S", [])
            self.assertEqual(len((Path(directory) / "io/requests.jsonl").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
