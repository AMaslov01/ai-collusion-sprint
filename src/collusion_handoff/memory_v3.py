"""Typed source metadata and transparent conservative facet verification."""

from copy import deepcopy
from hashlib import sha256

from .artifacts import canonical, fingerprint
from .memory import MODEL_EVENT_FIELDS, model_source_view, require, resolve_claims
from .memory_format import decode_format


FACETS = ("content", "attribution", "status_time", "conditions_units")


def resolve_v3(raw, archive, *, enforce_commit=True):
    claims = resolve_claims(raw, archive)
    events = {event["event_id"]: event for event in archive["events"]}
    for claim in claims:
        claim["source_roles"] = [{"event_id": ref["event_id"], "role": events[ref["event_id"]]["role"],
                                  "event_type": events[ref["event_id"]]["event_type"]} for ref in claim["source_refs"]]
        if enforce_commit and claim["status"] == "committed":
            allowed = archive.get("allowed_commit_event_types", [])
            require(any(events[ref["event_id"]]["event_type"] in allowed for ref in claim["source_refs"]),
                    "Committed status lacks an explicit permissible commit event")
    return claims


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
    view = model_source_view(archive, list(MODEL_EVENT_FIELDS))
    return canonical({"archive_id": fingerprint(archive), "source_view_id": fingerprint(view), **view}).decode()


def fixture_source(definition):
    identity = definition["fixture_id"]
    events = []
    for index, item in enumerate(definition["events"]):
        event_id = identity + "/" + item["id"]
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
                      "source_refs": [{"event_id": identity + "/" + ref, "quote": by_id[ref]["text"]} for ref in item["refs"]]}
                     for item in definition["candidates"]]}
    claims = resolve_v3(raw, archive, enforce_commit=False)
    for claim in claims:
        alias = definition["candidates"][claim["extraction_index"]]["alias"]
        claim["claim_id"] = identity + "/" + alias
    return {"archive": archive, "archive_sha256": fingerprint(archive)}, claims
