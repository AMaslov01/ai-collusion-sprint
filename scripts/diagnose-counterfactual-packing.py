"""Size-only cached diagnostic after failed C; never produces a primary package."""

import argparse
from collections import Counter
import os
from pathlib import Path

from transformers import AutoTokenizer

from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.memory import require
from collusion_handoff.memory_codec import ProvenanceCodec, pack_provenance_prefix
from collusion_handoff.memory_v3 import resolve_v3, resolve_evidence


def main():
    require(os.getuid() != 0 and os.environ.get("CUDA_VISIBLE_DEVICES") == "", "Requires non-root CPU Docker")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--raw-c", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Do not overwrite diagnostic")
    source, raw = read_json(args.source), read_json(args.raw_c)
    tokens = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    count = lambda text: len(tokens.encode(text, add_special_tokens=False))
    archive = source["archive"]
    claims = resolve_v3(raw, archive, enforce_commit=False, reference_resolver=resolve_evidence)
    order = [item["event_id"] for item in sorted(archive["events"], key=lambda item: item["order"])]
    packed, excluded, codec = pack_provenance_prefix(claims, count, order)
    result = {"status": "counterfactual_size_diagnostic_only", "new_model_calls": 0,
              "primary_C_still_failed_and_unavailable": True, "primary_artifacts_modified": False,
              "diagnostic_exception": "Only semantic status guard disabled; exact provenance, order, all text and refs retained",
              "selection": "Unchanged chronological whole-record prefix, cap2048/no backfill",
              "raw_claims": len(claims), "raw_tokens": count(ProvenanceCodec(claims, order).content(claims)),
              "fit_claims": len(packed), "fit_tokens": count(codec.content(packed)),
              "raw_subject_counts": dict(Counter(item["subject"] for item in claims)),
              "fit_subject_counts": dict(Counter(item["subject"] for item in packed)),
              "excluded": [{key: item[key] for key in ("claim_id", "subject", "extraction_index", "source_refs")} for item in excluded]}
    atomic_json(args.output, result)


if __name__ == "__main__":
    main()
