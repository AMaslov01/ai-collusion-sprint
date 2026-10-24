"""CPU-only developmental comparison on immutable prior source and raw C."""

from collections import Counter
import json
import os
from pathlib import Path

from transformers import AutoTokenizer

from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.memory import MODEL_EVENT_FIELDS, actor_record, claim_content, pack_claims, stage_messages, require
from collusion_handoff.memory_codec import ProvenanceCodec, compact_source, pack_provenance_prefix
from collusion_handoff.memory_v3 import messages_v31


def main():
    require(os.getuid() != 0 and os.environ.get("CUDA_VISIBLE_DEVICES") == "", "Non-root CPU Docker required")
    tokenizer = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    count = lambda text: len(tokenizer.encode(text, add_special_tokens=False))
    cases = {"old-group1": Path("/old"), "regression-v2": Path("/previous/regression-v2/memory"),
             "group-2": Path("/previous/heldout/memory/group-2"), "group-3": Path("/previous/heldout/memory/group-3")}
    assets = read_json(Path("/previous/regression-v2/memory/assets.json"))
    report = {"new_model_calls": 0, "known_cases_are_developmental": True,
              "context_comparison_assets": "Same frozen regression-v2 S/C assets for both source encodings, including older source",
              "selection_policy": "unchanged_chronological_whole_record_prefix_no_backfill", "cases": {}}
    for name, job in cases.items():
        source = read_json(job / "source.json")
        archive = source["archive"]
        order = [event["event_id"] for event in sorted(archive["events"], key=lambda item: item["order"])]
        result = {"source_roundtrip": True, "context": {}}
        for stage in ("S", "C"):
            old = messages_v31(stage, source, [], assets[stage])
            new = stage_messages(stage, archive, source["archive_sha256"], [], assets[stage]["schema"],
                                 assets[stage]["prompt"] + "\nSource event_rows follow event_columns; evidence_lines are [span_id, exact_text] pairs.\n",
                                 event_fields=list(MODEL_EVENT_FIELDS), source_view=compact_source(archive))
            rendered = lambda messages: len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                                           enable_thinking=False, return_dict=False))
            reserve = 4096 if stage == "S" else 8192
            result["context"][stage] = {"old_input_tokens": rendered(old), "new_input_tokens": rendered(new), "output_reserve": reserve,
                                         "new_total_reserved": rendered(new) + reserve, "context": 32768}
        resolved_path = job / "automatic-withheld/stages/C-resolved.json"
        if not resolved_path.exists():
            result["C"] = {"status": "unavailable_no_C_generation", "new_generation": False}
        else:
            claims = read_json(resolved_path)["unique_claims"]
            old_packed, _ = pack_claims(claims, count)
            packed, excluded, codec = pack_provenance_prefix(claims, count, order)
            full_codec = ProvenanceCodec(claims, order)
            full = json.loads(codec.content(packed))
            for subset in (packed, packed[::2], packed[1::2], []):
                reduced = json.loads(codec.content(subset))
                require(reduced["sources"] == full["sources"], "Dictionary changed after deletion")
                require(ProvenanceCodec.decode(reduced) == [actor_record(item) for item in subset], "Inverse roundtrip differs")
                before = {item["claim_id"]: row for item, row in zip(packed, full["records"])}
                require(all(before[item["claim_id"]] == row for item, row in zip(subset, reduced["records"])), "Retained record bytes changed")
            result["C"] = {"raw_count": len(claims), "old_raw_tokens": count(claim_content(claims)),
                           "new_raw_tokens": count(full_codec.content(claims)), "old_packed_count": len(old_packed),
                           "new_packed_count": len(packed), "new_packed_tokens": count(codec.content(packed)),
                           "excluded_claim_ids": [item["claim_id"] for item in excluded],
                           "raw_subjects": dict(Counter(item.get("subject", item.get("speaker", "unknown")) for item in claims)),
                           "new_packed_subjects": dict(Counter(item.get("subject", item.get("speaker", "unknown")) for item in packed)),
                           "inverse_roundtrip": True, "dictionary_and_retained_record_stability": True}
        report["cases"][name] = result
    atomic_json(Path("/work/codec-measurement.json"), report)


if __name__ == "__main__":
    main()
