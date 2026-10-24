from copy import deepcopy
import json
import unittest

from collusion_handoff.memory import actor_record, MemoryError
from collusion_handoff.memory_codec import ProvenanceCodec, pack_provenance_prefix, compact_source, expand_source
from collusion_handoff.memory_v3 import evidence_view
from test_memory import fixture


class CodecTests(unittest.TestCase):
    def claims(self):
        ref = {"event_id": "event-1", "source_hash": "a" * 64, "start": 0, "end": 17}
        return [{"text": "A said café; the blank field is unspecified.", "source_refs": [deepcopy(ref), deepcopy(ref)]},
                {"text": "B rejected the earlier proposal.", "source_refs": [{**ref, "start": 18, "end": 39}]}]

    def test_actor_roundtrip_preserves_text_all_refs_order_and_duplicates(self):
        claims = self.claims()
        codec = ProvenanceCodec(claims, ["unused", "event-1"])
        self.assertEqual(codec.decode(json.loads(codec.content(claims))), [actor_record(item) for item in claims])
        self.assertEqual(len(codec.sources), 1)

    def test_deletion_keeps_dictionary_and_exact_record_bytes(self):
        claims = self.claims()
        codec = ProvenanceCodec(claims, ["unused", "event-1"])
        full = json.loads(codec.content(claims))
        reduced = json.loads(codec.content(claims[1:]))
        self.assertEqual(full["sources"], reduced["sources"])
        self.assertEqual(full["records"][1:], reduced["records"])
        self.assertEqual(json.loads(codec.content([]))["records"], [])

    def test_prefix_does_not_backfill_later_small_record(self):
        claims = self.claims() + [{"text": "Tiny.", "source_refs": self.claims()[0]["source_refs"]}]
        claims[1]["text"] = "x" * 10000
        limit = len(ProvenanceCodec(claims[:1], ["event-1"]).content(claims[:1]))
        selected, excluded, codec = pack_provenance_prefix(claims, len, ["event-1"], limit)
        self.assertEqual(selected, claims[:1])
        self.assertEqual(excluded, claims[1:])

    def test_conflicting_source_identity_fails(self):
        claims = self.claims()
        claims[1]["source_refs"][0]["source_hash"] = "b" * 64
        with self.assertRaises(MemoryError):
            ProvenanceCodec(claims, ["event-1"])

    def test_alias_is_source_order_not_claim_or_selected_order(self):
        claims = self.claims()
        claims[0]["source_refs"] = [{**claims[0]["source_refs"][0], "event_id": "event-2"}]
        codec = ProvenanceCodec(claims, ["event-1", "event-2"])
        first = json.loads(codec.content(claims[:1]))
        self.assertEqual(first["records"][0]["source_refs"][0][0], 1)

    def test_lossless_source_roundtrip_preserves_every_character(self):
        source, _ = fixture("é\r\nrepeated\nrepeated\nlast line")
        archive = source["archive"]
        self.assertEqual(expand_source(compact_source(archive)), evidence_view(archive))


if __name__ == "__main__":
    unittest.main()
