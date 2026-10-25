"""Mechanically resolve a sealed review's event IDs; make no semantic decisions."""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path

from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.memory import require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-draft", type=Path, required=True)
    parser.add_argument("--expected-draft-sha256", required=True)
    parser.add_argument("--source-prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(os.getuid() != 0 and Path("/.dockerenv").exists() and os.environ.get("CUDA_VISIBLE_DEVICES") == "" and
            {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}, "Nonroot CPU Docker without network required")
    require(not args.output.exists(), "Never overwrite a sealed review resolution")
    require(sha256(args.semantic_draft.read_bytes()).hexdigest() == args.expected_draft_sha256, "Sealed semantic draft changed")
    sources, events = {}, {}
    for number in (8, 9):
        path = args.source_prefix / f"prefix/group-{number}/source.json"
        source = read_json(path)
        require(fingerprint(source["archive"]) == source["archive_sha256"], "Source archive hash changed")
        sources[number] = {"source_file_sha256": sha256(path.read_bytes()).hexdigest(), "archive_sha256": source["archive_sha256"]}
        for event in source["archive"]["events"]:
            require(event["event_id"] not in events and sha256(event["content"].encode()).hexdigest() == event["content_sha256"],
                    "Source event ID/hash mismatch")
            events[event["event_id"]] = event
    draft = read_json(args.semantic_draft)
    result = deepcopy(draft)
    resolved_count = 0

    def event(identity, group):
        require(identity in events, "Review cites a nonexistent source event")
        item = events[identity]
        require(group is None or item["group_id"] == group, "Review cites the other world's event")
        return item

    def walk(value, group=None):
        nonlocal resolved_count
        if isinstance(value, list):
            for item in value:
                walk(item, group)
        elif isinstance(value, dict):
            if "group_id" in value:
                group = int(value["group_id"])
            if "source_file_sha256" in value:
                matching = [number for number, spec in sources.items() if spec["source_file_sha256"] == value["source_file_sha256"]]
                require(len(matching) == 1 and (group is None or group == matching[0]), "Review source file hash mismatch")
                group = matching[0]
            if "archive_sha256" in value:
                require(group in sources and value["archive_sha256"] == sources[group]["archive_sha256"], "Review archive hash mismatch")
            for key, item in list(value.items()):
                if key.endswith("event_ids"):
                    require(isinstance(item, list), "Event IDs must be an explicit list")
                    selected = [event(identity, group) for identity in item]
                    if key.endswith("evidence_event_ids"):
                        spans = [{"event_id": entry["event_id"], "start": 0, "end": len(entry["content"].encode()),
                                  "quote": entry["content"], "content_sha256": entry["content_sha256"]} for entry in selected]
                        require("evidence" not in value or value["evidence"] in ([], spans), "Do not rewrite existing evidence")
                        value["evidence"] = spans
                        resolved_count += len(spans)
                elif key.endswith("event_id") and isinstance(item, str):
                    event(item, group)
                else:
                    walk(item, int(key) if key in {"8", "9"} else group)

    walk(result)
    require(resolved_count > 0, "No evidence-event IDs were resolved")
    os.umask(0o077)
    atomic_json(args.output, result)
    receipt = {"schema": "sealed_source_review_span_resolution_v1", "semantic_draft_sha256": args.expected_draft_sha256,
        "output_sha256": sha256(args.output.read_bytes()).hexdigest(), "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "sources": sources, "resolved_evidence_spans": resolved_count, "semantic_decisions_changed": False,
        "source_only": True, "memory_outputs_opened": False, "new_model_calls": 0}
    atomic_json(args.output.with_suffix(".receipt.json"), receipt)
    print(json.dumps({"status": "resolved_without_semantic_choices", "output_sha256": receipt["output_sha256"]}))


if __name__ == "__main__":
    main()
