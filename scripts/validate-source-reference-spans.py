"""Verify sealed source-reference bytes and exhaustive field census in CPU Docker."""

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "source", "index", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    assert os.getuid() != 0 and Path("/.dockerenv").exists()
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == "" and not list(Path("/dev").glob("nvidia[0-9]*"))
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
    assert not args.output.exists()
    reference, source, index = (json.loads(path.read_text()) for path in (args.reference, args.source, args.index))
    assert reference["source_file_sha256"] == index["source_file_sha256"] == digest(args.source)
    assert reference["neutral_index_sha256"] == digest(args.index)
    archive = source["archive"]
    canonical = json.dumps(archive, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    assert reference["archive_sha256"] == source["archive_sha256"] == index["archive_sha256"] == sha256(canonical).hexdigest()
    assert reference["checkpoint_id"] == archive["checkpoint_id"] == index["checkpoint_id"]
    events = {event["event_id"]: event for event in archive["events"]}
    assert len(events) == len(archive["events"])
    assert reference["reviewed_event_ids"] == [event["event_id"] for event in archive["events"]]
    provenance = reference["provenance"]
    assert provenance["human_gold"] is False and provenance["memory_outputs_opened"] is False
    assert provenance["future_queries_opened"] is False and provenance["hidden_answers_opened"] is False
    span_count = 0

    def verify_spans(value):
        nonlocal span_count
        if isinstance(value, list):
            for item in value:
                verify_spans(item)
        elif isinstance(value, dict):
            if {"event_id", "start", "end", "quote", "source_hash"} <= value.keys():
                event = events[value["event_id"]]
                body = event["content"].encode()
                assert value["source_hash"] == event["content_sha256"] == sha256(body).hexdigest()
                assert type(value["start"]) is int and type(value["end"]) is int
                assert 0 <= value["start"] < value["end"] <= len(body)
                assert body[value["start"]:value["end"]].decode() == value["quote"]
                if "role" in value:
                    assert value["role"] == event["role"]
                span_count += 1
            for item in value.values():
                verify_spans(item)

    verify_spans(reference)
    fields = {(event["event_id"], json.dumps(field["json_path"])): field
              for event in index["events"] for field in event["json_fields"]}
    census = reference["raw_field_census"]
    assert len(census) == len(fields) == index["leaf_field_count"]
    assert len({row["candidate_id"] for row in census}) == len(census)
    keys = [(row["event_id"], json.dumps(row["json_path"])) for row in census]
    assert len(set(keys)) == len(keys) and set(keys) == set(fields)
    units = {unit["unit_id"]: unit for unit in reference["units"]}
    assert len(units) == len(reference["units"])
    for row, key in zip(census, keys):
        field = fields[key]
        assert row["value"] == field["value"]
        assert (row["span"]["start"], row["span"]["end"], row["span"]["quote"]) == (
            field["start_utf8"], field["end_utf8"], field["raw_json_literal"])
        assert set(row["linked_unit_ids"]) <= units.keys()
        assert row["disposition"] and row["reason"]
    counts = dict(Counter(unit["stratum"] for unit in units.values()))
    assert all(counts.get(stratum, 0) == count for stratum, count in reference["counts"].items())
    report = {"schema": "source_reference_raw_span_validation_v1", "status": "passed",
              "reference_sha256": digest(args.reference), "source_sha256": digest(args.source),
              "neutral_index_sha256": digest(args.index), "validator_sha256": digest(Path(__file__)),
              "events": len(events), "raw_fields": len(fields), "exact_utf8_spans_checked": span_count,
              "units": len(units), "stratum_counts": counts, "new_model_calls": 0,
              "semantic_labels_changed": False, "human_gold": False}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
