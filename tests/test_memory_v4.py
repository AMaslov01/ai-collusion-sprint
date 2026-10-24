import json
import unittest

from collusion_handoff.memory import MemoryError, build_packages
from collusion_handoff.memory_codec import ProvenanceCodec, compact_candidates, expand_candidates
from collusion_handoff.memory_v4 import constrained_payload, decode_v4, full_delivery_v4, messages_v4, record_packer, resolve_v4
from test_memory import fixture


class MemoryV4Tests(unittest.TestCase):
    def source_and_claims(self):
        source, _ = fixture("Plan A.\nPlan B.\n")
        claims = [{"text": f"A issued Plan {letter}.", "content_type": "plan_state", "subject": "traveler",
                   "time_scope": "prefix", "status": "issued_plan", "source_refs": [{"event_id": "event-1", "span_id": f"line-{number:04d}"}]}
                  for number, letter in enumerate(("A", "B"), 1)]
        return source, claims

    def test_candidate_table_roundtrip_preserves_research_fields_and_exact_refs(self):
        source, raw = self.source_and_claims()
        claims = resolve_v4({"claims": raw}, source["archive"])
        self.assertEqual(expand_candidates(compact_candidates(claims, source["archive"])), claims)

    def test_requirement_cannot_inherit_provided_final_from_initial_context(self):
        source, raw = self.source_and_claims()
        source["archive"]["events"][0]["event_type"] = "initial_given_base_context"
        raw[0].update({"content_type": "public_requirement", "status": "provided_final"})
        with self.assertRaises(MemoryError):
            resolve_v4({"claims": raw}, source["archive"])
        raw[0]["status"] = "requested"
        self.assertEqual(resolve_v4({"claims": raw}, source["archive"])[0]["status"], "requested")

    def test_declared_public_rule_can_remain_stated_without_becoming_user_request(self):
        source, raw = self.source_and_claims()
        raw[0].update({"text": "The public cap is stated in the policy.", "content_type": "public_requirement", "status": "stated"})
        self.assertEqual(resolve_v4({"claims": raw}, source["archive"])[0]["status"], "stated")

    def test_constrained_wire_schema_has_no_desired_verdict_and_prose_is_failure(self):
        schema = {"type": "object", "properties": {"value": {"enum": ["allow", "exclude"]}}, "required": ["value"], "additionalProperties": False}
        body = constrained_payload("pinned", {"temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}, [], schema, 8192)
        self.assertEqual(body["response_format"]["json_schema"]["schema"], schema)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        with self.assertRaises(json.JSONDecodeError):
            decode_v4("P", 'Explanation: {"value":"allow"}', schema)

    def test_actual_builder_path_uses_frozen_dictionary_after_deletion(self):
        source, raw = self.source_and_claims()
        assets = {stage: {"prompt": "Use source only.", "schema": {"type": "object"}} for stage in ("S", "C", "V", "P")}
        stored, calls = {}, []
        def generate(stage, messages):
            calls.append(stage)
            data = json.loads(messages[1]["content"])
            self.assertIn("event_rows", data)
            if stage == "S":
                return json.dumps({"sentences": ["A issued two plans."]})
            if stage == "C":
                return json.dumps({"claims": raw})
            candidates = expand_candidates(data["candidates"], source["archive"])
            if stage == "V":
                return json.dumps({"verdicts": [{"claim_id": item["claim_id"],
                    "actor_facets": {name: {"verdict": "supported" if index == 0 else "contradicted", "reason": "Fixture evidence"}
                                     for name in ("content", "attribution", "status_time", "conditions_units")},
                    "research_metadata": {"verdict": "supported", "reason": "Known metadata"}}
                    for index, item in enumerate(candidates)]})
            return json.dumps({"verdicts": [{"claim_id": item["claim_id"], "label": "allow", "reason": "Historical plan"} for item in candidates]})
        contract = {"packing": {"archive_wrapper": "Historical data: {content}"}}
        build_packages(source, contract, assets, generate, len, stored.__setitem__, decode=decode_v4,
                       resolve=resolve_v4, full_archive_content=full_delivery_v4, message_builder=messages_v4,
                       record_packer=record_packer(source["archive"]))
        self.assertEqual(calls, ["S", "C", "V", "P"])
        packages = [json.loads(stored[f"packages/{arm}.json"]["content"]) for arm in ("C", "CV", "CVP")]
        self.assertEqual(packages[0]["sources"], packages[1]["sources"])
        self.assertEqual(packages[1]["sources"], packages[2]["sources"])
        self.assertEqual(packages[0]["records"][:1], packages[1]["records"])
        self.assertEqual(ProvenanceCodec.decode(packages[1]), ProvenanceCodec.decode(packages[2]))


if __name__ == "__main__":
    unittest.main()
