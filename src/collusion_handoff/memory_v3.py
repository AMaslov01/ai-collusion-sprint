"""Typed source metadata and transparent conservative facet verification."""

from copy import deepcopy
from hashlib import sha256

from .artifacts import canonical, fingerprint
from .memory import MODEL_EVENT_FIELDS, model_source_view, require, resolve_claims, stage_messages
from .memory_format import decode_format


FACETS = ("content", "attribution", "status_time", "conditions_units")


def resolve_v3(raw, archive, *, enforce_commit=True, reference_resolver=None):
    claims = resolve_claims(raw, archive, reference_resolver=reference_resolver)
    events = {event["event_id"]: event for event in archive["events"]}
    for claim in claims:
        claim["source_roles"] = [{"event_id": ref["event_id"], "role": events[ref["event_id"]]["role"],
                                  "event_type": events[ref["event_id"]]["event_type"]} for ref in claim["source_refs"]]
        if enforce_commit and claim["status"] == "committed":
            allowed = archive.get("allowed_commit_event_types", [])
            require(any(events[ref["event_id"]]["event_type"] in allowed for ref in claim["source_refs"]),
                    "Committed status lacks an explicit permissible commit event")
        if enforce_commit and claim["status"] == "provided_final":
            require(any(events[ref["event_id"]]["event_type"] == "initial_given_base_context" for ref in claim["source_refs"]),
                    "provided_final requires the initially given base-plan context; A outputs are issued_plan")
    return claims


def evidence_lines(event):
    content = event["content"]
    require(sha256(content.encode()).hexdigest() == event["content_sha256"], "Original source bytes changed")
    lines, offset = [], 0
    for index, text in enumerate(content.splitlines(keepends=True) or [""]):
        end = offset + len(text.encode())
        lines.append({"span_id": f"line-{index+1:04d}", "text": text, "start": offset, "end": end})
        require(content.encode()[offset:end] == text.encode(), "Evidence UTF-8 boundary mismatch")
        offset = end
    require("".join(line["text"] for line in lines) == content, "Evidence presentation lost original characters")
    return lines


def evidence_view(archive):
    view = model_source_view(archive, list(MODEL_EVENT_FIELDS))
    original = {event["event_id"]: event for event in archive["events"]}
    for event in view["source_events"]:
        event.pop("content")
        event["evidence_lines"] = [{"span_id": line["span_id"], "text": line["text"]} for line in evidence_lines(original[event["event_id"]])]
    view["source_descriptor_version"] = "lossless_evidence_lines_v1"
    return view


def resolve_evidence(ref, event):
    lines = {line["span_id"]: line for line in evidence_lines(event)}
    first, last = lines.get(ref["span_id"]), lines.get(ref.get("end_span_id", ref["span_id"]))
    require(first is not None and last is not None and last["end"] > first["start"] and last["start"] >= first["start"], "Unknown, empty or reversed evidence span")
    return {"event_id": event["event_id"], "start": first["start"], "end": last["end"], "source_hash": event["content_sha256"]}


def resolve_v31(raw, archive):
    claims = resolve_v3(raw, archive, reference_resolver=resolve_evidence)
    by_id = {event["event_id"]: event for event in archive["events"]}
    for claim in claims:
        claim["source_evidence"] = [{"event_id": ref["event_id"], "span_ids": [line["span_id"] for line in evidence_lines(by_id[ref["event_id"]])
                                     if line["start"] < ref["end"] and line["end"] > ref["start"]]} for ref in claim["source_refs"]]
    return claims


def messages_v31(stage, source, candidates, asset):
    return stage_messages(stage, source["archive"], source["archive_sha256"], candidates, asset["schema"], asset["prompt"],
                          event_fields=list(MODEL_EVENT_FIELDS), source_view=evidence_view(source["archive"]))


def aggregate(values):
    for state in ("error", "contradicted", "insufficient_evidence"):
        if state in values:
            return state
    require(all(value == "supported" for value in values), "Unknown or inapplicable facet verdict")
    return "supported"


def derive_verification(value):
    result = deepcopy(value)
    for row in result["verdicts"]:
        facets = row["actor_facets"]
        # Content and attribution are universal for a nonempty asserted record.
        invalid_na = [name for name in ("content", "attribution", "status_time") if facets[name]["verdict"] == "not_applicable"]
        if row["research_metadata"]["verdict"] == "not_applicable":
            invalid_na.append("research_metadata")
        actor_values = [facets[name]["verdict"] for name in FACETS]
        row["actor_text_verdict"] = aggregate(actor_values) if not invalid_na else "error"
        row["research_metadata_verdict"] = row["research_metadata"]["verdict"]
        row["invalid_not_applicable_facets"] = invalid_na
        row["verdict"] = "error" if invalid_na else aggregate(actor_values + [row["research_metadata"]["verdict"]])
        row["aggregation_version"] = "v3_conservative_actor_and_metadata_union"
    return result


def decoder_v3(persist):
    def decode(stage, text, schema):
        value, receipt = decode_format(text, schema)
        persist(f"stages/{stage}-decoding.json", {**receipt, "stage": stage, "derivation": "facet_union" if stage == "V" else "none"})
        return derive_verification(value) if stage == "V" else value
    return decode


def full_delivery(archive):
    view = evidence_view(archive)
    return canonical({"archive_id": fingerprint(archive), "source_view_id": fingerprint(view), **view}).decode()


def fixture_source(definition):
    identity = f"fixture-{int(definition['fixture_id'][1:3]):02d}"
    event_ids = {item["id"]: f"{identity}/event-{index+1:02d}" for index, item in enumerate(definition["events"])}
    events = []
    for index, item in enumerate(definition["events"]):
        event_id = event_ids[item["id"]]
        events.append({"event_id": event_id, "origin_event_id": event_id, "role": item["role"],
                       "event_type": item["type"], "group_id": "controlled_fixture", "traveler_idx": 0,
                       "turn_idx": index, "order": index, "content": item["text"],
                       "content_sha256": sha256(item["text"].encode()).hexdigest(), "derived_from": [],
                       "source_version": {"fixture_version": "controlled_memory_facets_v3"}})
    archive = {"schema_version": "controlled_source_v3", "checkpoint_id": identity,
               "source_policy_id": "controlled_semantic_v3", "events": events, "allowed_commit_event_types": []}
    by_id = {item["id"]: item for item in definition["events"]}
    raw = {"claims": [{"text": item["text"], "content_type": "historical_speech_act" if "agreement" in item["status"] else "other",
                      "subject": item["subject"], "time_scope": "supplied fixture history", "status": item["status"],
                      "source_refs": [{"event_id": event_ids[ref], "quote": by_id[ref]["text"]} for ref in item["refs"]]}
                     for item in definition["candidates"]]}
    claims = resolve_v3(raw, archive, enforce_commit=False)
    for claim in claims:
        claim["claim_id"] = f"{identity}/claim-{claim['extraction_index']+1:02d}"
    return {"archive": archive, "archive_sha256": fingerprint(archive)}, claims


def fixture_eligible(definition, claims):
    indices = {index for index, item in enumerate(definition["candidates"]) if item["alias"] in definition["P_eligible"]}
    return [claim for claim in claims if claim["extraction_index"] in indices]


def fixture_mapping(definition, source, claims):
    by_index = {claim["extraction_index"]: claim["claim_id"] for claim in claims}
    return {"audit_fixture_id": definition["fixture_id"], "model_checkpoint_id": source["archive"]["checkpoint_id"],
            "aliases_to_model_claim_ids": {item["alias"]: by_index[index] for index, item in enumerate(definition["candidates"])},
            "event_ids": {item["id"]: source["archive"]["events"][index]["event_id"] for index, item in enumerate(definition["events"])}}
