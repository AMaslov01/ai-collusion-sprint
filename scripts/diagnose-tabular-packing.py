"""Cached size-only comparison; never constructs a primary delivered package."""

from collections import Counter
import json
import os
from pathlib import Path

from transformers import AutoTokenizer

from collusion_handoff.artifacts import atomic_json, canonical, read_json
from collusion_handoff.memory import actor_record, require
from collusion_handoff.memory_codec import ProvenanceCodec
from collusion_handoff.memory_v3 import resolve_v3, resolve_evidence


def encode(claims, order, *, shared_spans):
    codec = ProvenanceCodec(claims, order)
    original = json.loads(codec.content(claims))
    value = {"format": "diagnostic_actor_table_v1", "sources": original["sources"],
             "record_columns": ["text", "source_refs"], "rows": []}
    spans, indices = [], {}
    for record in original["records"]:
        refs = []
        for ref in record["source_refs"]:
            if shared_spans:
                key = tuple(ref)
                if key not in indices:
                    indices[key] = len(spans)
                    spans.append(ref)
                refs.append(indices[key])
            else:
                refs.append(ref)
        value["rows"].append([record["text"], refs])
    if shared_spans:
        value["spans"] = spans
    restored = []
    for text, refs in value["rows"]:
        restored.append({"text": text, "source_refs": [value["spans"][ref] for ref in refs] if shared_spans else refs})
    inverse = ProvenanceCodec.decode({"format": "provenance_dictionary_v1", "sources": value["sources"], "records": restored})
    require(inverse == [actor_record(claim) for claim in claims], "Text/ordered-ref inverse differs")
    # Deletion preserves every dictionary and retained row, including repeated refs.
    for selected in (value["rows"][::2], value["rows"][1::2], []):
        descendant = {**value, "rows": selected}
        require(descendant["sources"] == value["sources"] and descendant.get("spans") == value.get("spans"), "Dictionary changed")
        require(all(canonical(row) in [canonical(original) for original in value["rows"]] for row in selected), "Retained row changed")
    return canonical(value).decode()


def main():
    require(os.getuid() != 0 and os.environ.get("CUDA_VISIBLE_DEVICES") == "", "Non-root CPU Docker required")
    tokens = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    count = lambda text: len(tokens.encode(text, add_special_tokens=False))
    jobs = {"actual-known1-raw56": Path("/cycle/development-memory-v1/memory/known-group-1"),
            "old-regression20": Path("/previous/regression-v2/memory"), "old-group3-19": Path("/previous/heldout/memory/group-3")}
    report = {"new_model_calls": 0, "scope": "Size-only diagnostic; primary failures remain unavailable, no delivery or selection-policy change", "cases": {}}
    for name, path in jobs.items():
        source = read_json(path / "source.json")
        archive = source["archive"]
        resolved = path / "automatic-withheld/stages/C-resolved.json"
        if resolved.exists():
            claims = read_json(resolved)["unique_claims"]
        else:
            raw = read_json(path / "automatic-withheld/stages/C.json")
            claims = resolve_v3(raw, archive, enforce_commit=False, reference_resolver=resolve_evidence)
        order = [event["event_id"] for event in sorted(archive["events"], key=lambda item: item["order"])]
        result = {"raw_count": len(claims), "existing_codec_raw_tokens": count(ProvenanceCodec(claims, order).content(claims)), "variants": {}}
        for shared in (False, True):
            packed = []
            for claim in claims:
                trial = packed + [claim]
                if count(encode(trial, order, shared_spans=shared)) > 2048:
                    break
                packed = trial
            result["variants"]["row_table_shared_spans" if shared else "row_table"] = {
                "raw_tokens": count(encode(claims, order, shared_spans=shared)), "fit_count": len(packed),
                "fit_tokens": count(encode(packed, order, shared_spans=shared)),
                "raw_subjects": dict(Counter(claim["subject"] for claim in claims)),
                "fit_subjects": dict(Counter(claim["subject"] for claim in packed)),
                "excluded_claim_ids": [claim["claim_id"] for claim in claims[len(packed):]],
                "full_text_and_ordered_ref_roundtrip": True, "frozen_dictionary_and_retained_row_nesting": True}
        report["cases"][name] = result
    atomic_json(Path("/work/diagnostic.json"), report)


if __name__ == "__main__":
    main()
