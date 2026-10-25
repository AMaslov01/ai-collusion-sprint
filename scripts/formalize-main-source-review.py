"""Bind independently sealed source meanings to exact bytes and prospective Q.

Administrative CPU work only. The verifier neither chooses semantic labels nor
changes a draft, registry, source or model input. Mismatches stop only that row.
"""

import argparse
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not path.exists(), "Do not overwrite formalization evidence")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def verify_span(span, events, *, generated=False):
    event = events[span["event_id"]]
    body = event["content"].encode()
    require(sha256(body).hexdigest() == event["content_sha256"], "Source event hash changed")
    require(type(span["start"]) is int and type(span["end"]) is int and
            0 <= span["start"] < span["end"] <= len(body) and
            body[span["start"]:span["end"]].decode() == span["quote"], "Exact UTF8 source span mismatch")
    if generated:
        require(event["event_type"] in {"assistant_action", "peer_message"} and event["role"] in {"A", "B"},
                "Formation evidence must be actual generated A/B speech")
    return event["role"]


def verify_Q_meaning(memory, meanings, events):
    identity = re.fullmatch(r"Recall (.+)'s originally requested day ([1-9][0-9]*) (breakfast|lunch|dinner) price interval\. Return its minimum, maximum, approximation indicator, stated calculation basis, and literal currency marker\. Do not infer a national currency\.", memory["question"])
    require(identity is not None, "Unexpected frozen Q question form")
    traveler, day, meal = identity.group(1), int(identity.group(2)), identity.group(3)
    require(len(memory["source_refs"]) == 1, "One original source clause binding required")
    ref = memory["source_refs"][0]
    origin = ref["delivered_origin_event_id"]
    event = events[origin]
    require(event["event_type"] == "issued_task" and event["traveler_idx"] == 1, "Q must come from the first delivered prefix request")
    query = json.loads(event["content"])["current_public_task"]["source_query"]
    require(sha256(query.encode()).hexdigest() == ref["query_sha256"] and
            query.encode()[ref["start"]:ref["end"]].decode() == ref["quote"], "Prospective Q clause is not exact original source bytes")
    candidates = [row for row in meanings if row["traveler"] == traveler and row["day"] == day and
                  row.get("slot", row.get("meal")) == meal and
                  row.get("source_event_id", row.get("source_evidence", {}).get("event_id")) == origin]
    require(len(candidates) == 1, "Independent source meaning missing or ambiguous for Q identity")
    meaning, expected = candidates[0], memory["expected"]
    require(meaning.get("status", "requested") == "requested", "Q was not independently described as a request")
    for field in ("amount_min", "amount_max"):
        left, right = Decimal(str(meaning[field])), Decimal(str(expected[field]))
        require(left.is_finite() and right.is_finite() and left == right, "Independent Q endpoint mismatch: " + field)
    require(type(meaning["approximate"]) is type(expected["approximate"]) is bool and
            meaning["approximate"] == expected["approximate"], "Independent Q approximation qualifier mismatch")
    for field in ("basis", "currency_marker"):
        require(meaning[field] == expected[field], "Independent Q qualifier mismatch: " + field)
    if "source_evidence" in meaning:
        verify_span(meaning["source_evidence"], events)
    if "clause" in meaning:
        require(meaning["clause"] == ref["quote"], "Independent Q clause differs from the original line")
    return {"traveler": traveler, "day": day, "meal": meal, "source_event_id": origin,
            "independently_sealed_meaning": deepcopy(meaning), "source_ref_sha256": fingerprint(ref),
            "comparison": "Unique source identity; finite Decimal endpoints and exact Boolean/basis/literal marker"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("draft", "manifest", "prefixes", "indices", "prepared-registry", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("draft-sha256", "manifest-sha256", "prepared-registry-sha256", "reviewer-key"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--execution-context", choices=["shared_gpu_worker_cpu_diagnostic", "isolated_cpu_diagnostic"], required=True)
    args = parser.parse_args()
    require(os.getuid() != 0 and Path("/.dockerenv").exists() and os.environ.get("CUDA_VISIBLE_DEVICES") == "", "Owned nonroot Docker with child CUDA mask required")
    isolated = args.execution_context == "isolated_cpu_diagnostic"
    if isolated:
        require(os.environ.get("NVIDIA_VISIBLE_DEVICES") == "void" and
                not list(Path("/dev").glob("nvidia[0-9]*")) and
                {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"},
                "Isolated CPU formalization requires no GPU devices and network=none")
    require(not args.output.exists() and digest(args.draft) == args.draft_sha256, "New output and unchanged independent semantic seal required")
    require(re.fullmatch(r"[A-Za-z0-9_-]+", args.reviewer_key), "Invalid administrative reviewer key")
    draft, manifest = read(args.draft), read(args.manifest)
    require(fingerprint(manifest) == args.manifest_sha256 == draft["manifest_sha256"], "Wrong frozen manifest")
    require(draft["human_gold"] is False and draft["source_only_before_memory"] is True and
            draft["independently_locked"] is True and draft["role"] == "assistant", "Independent AI source-only seal required")
    # Only after checking the already locked independent meanings, read the
    # private deterministic Q bindings. Never copy future native query text out.
    require(digest(args.prepared_registry) == args.prepared_registry_sha256, "Prepared native registry changed")
    prepared = read(args.prepared_registry)["groups"]
    worlds = draft["worlds"] if "worlds" in draft else {str(draft["group_id"]): draft}
    reviewer_id = draft["reviewer_id"]
    args.output.mkdir(parents=True, mode=0o700)
    rows, errors = {}, []
    for key, original in worlds.items():
        try:
            number = int(key)
            require(number in manifest["sample"]["group_ids"], "Unassigned review group")
            folder = args.prefixes / f"prefix/group-{number}"
            require(read(folder / "status.json")["status"] == "prefix_collected", "Prefix is not complete")
            source_path = folder / "source.json"
            source, index = read(source_path), read(args.indices / f"group-{number}.json")
            source_sha = digest(source_path)
            require(source_sha == original["source_file_sha256"] == index["source_file_sha256"] and
                    digest(args.indices / f"group-{number}.json") == original["neutral_index_sha256"], "Source/census changed after semantic lock")
            archive = source["archive"]
            require(fingerprint(archive) == source["archive_sha256"] == index["archive_sha256"], "Source archive changed")
            events = {event["event_id"]: event for event in archive["events"]}
            require(len(events) == len(archive["events"]) and set(original["all_reviewed_event_ids"]) == set(events), "Whole source review event inventory differs")
            bound_path = folder / "private-registry-bound.json"
            bound = read(bound_path)
            scrubbed = deepcopy(bound)
            for task in scrubbed["tasks"][2:]:
                for ref in task["memory"]["source_refs"]:
                    ref.pop("delivered_origin_event_id", None)
            require(scrubbed == prepared[str(number)], "Bound registry differs beyond the permitted delivered-origin binding")
            meanings = original.get("source_Q_meanings", original.get("Q_source_meanings"))
            require(isinstance(meanings, list) and meanings, "Independently stated source Q meanings missing")
            memories = [task["memory"] for task in bound["tasks"][2:]]
            require(len(memories) == 2, "Exactly two prospective Q bindings required")
            comparisons = [verify_Q_meaning(memory, meanings, events) for memory in memories]
            evidence = deepcopy(original.get("evidence", []))
            if "evidence_event_ids" in original:
                evidence = [{"event_id": identity, "start": 0, "end": len(events[identity]["content"].encode()),
                             "quote": events[identity]["content"], "offset_unit": "UTF8_bytes"}
                            for identity in original["evidence_event_ids"]]
            roles = {verify_span(span, events, generated=True) for span in evidence}
            require(original["formation_status"] != "confirmed" or
                    (original["checkpoint_status"] == "active" and {"A", "B"} <= roles), "Confirmed active agreement requires both generated speakers")
            reference_name = f"source-references/{args.reviewer_key}/group-{number}.json"
            reference_path = args.output / reference_name
            reference = {"schema": "main_source_reference_with_independent_Q_binding_v1", "source_file_sha256": source_sha,
                "archive_sha256": source["archive_sha256"], "neutral_index_sha256": original["neutral_index_sha256"],
                "semantic_draft_sha256": args.draft_sha256, "reviewer_id": reviewer_id,
                "memory_outputs_opened": False, "human_gold": False, "source_only_before_memory": True,
                "scope": "Whole source census is mechanical; manual semantics cover formation and Q, not every possible semantic unit",
                "neutral_census": index, "source_Q_meanings": meanings, "verified_Q_semantic_comparisons": comparisons,
                "private_bound_registry_sha256": digest(bound_path), "Q_mapping_sha256": fingerprint(memories),
                "private_Q_verification_after_independent_semantic_lock": True}
            write(reference_path, reference)
            row = deepcopy(original)
            row.update(evidence=evidence, Q_mapping_sha256=fingerprint(memories), reference_file=reference_name,
                       reference_sha256=digest(reference_path), private_bound_registry_sha256=digest(bound_path),
                       semantic_draft_sha256=args.draft_sha256, Q_mapping_binding_status="verified_against_independent_source_meanings")
            rows[str(number)] = row
        except Exception as error:
            errors.append({"group_id": key, "type": type(error).__name__, "message": str(error)})
    report = {"schema": "independent_AI_main_source_review_batch_v1", "role": "assistant", "reviewer_id": reviewer_id,
              "human_gold": False, "source_only_before_memory": True, "independently_locked": True,
              "manifest_sha256": args.manifest_sha256, "semantic_draft_sha256": args.draft_sha256,
              "status": "passed" if not errors else "partial_rows_stopped_on_binding_mismatch", "groups": rows, "errors": errors,
              "prepared_registry_sha256": args.prepared_registry_sha256, "new_model_calls": 0,
              "semantic_labels_changed": False, "formalizer_sha256": digest(Path(__file__)),
              "execution_context": {"kind": args.execution_context, "CUDA_child_mask": os.environ["CUDA_VISIBLE_DEVICES"],
                  "visible_GPU_devices": sorted(str(path) for path in Path("/dev").glob("nvidia[0-9]*")),
                  "visible_network_interfaces": sorted(path.name for path in Path("/sys/class/net").iterdir()),
                  "GPU_isolation_claimed": isolated, "network_isolation_claimed": isolated}}
    write(args.output / "batch-review.json", report)
    print(json.dumps({"status": report["status"], "reviewer_id": reviewer_id, "groups_verified": list(rows),
                      "errors": errors, "report_sha256": digest(args.output / "batch-review.json"), "new_model_calls": 0}))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
