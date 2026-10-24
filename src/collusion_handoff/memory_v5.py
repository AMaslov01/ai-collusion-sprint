"""Budget-aware extraction and typed legacy provenance; immutable v4 is preserved."""

from hashlib import sha256
import json

from .artifacts import canonical, fingerprint
from .memory import require
from .memory_codec import compact_source
from .memory_v3 import evidence_lines, resolve_evidence, resolve_v3
from .memory_v4 import messages_v4
from .source_segments import describe_source_event, has_explicit_supplied_plan_evidence


SEGMENT_TYPES = {"initial_given_base_context", "base_query", "provided_base_plan"}
SEGMENT_NOTE = (
    "\nTyped source segments separate original query_request, explicit_finality_notice, "
    "provided_plan_fields and context without changing any original text or references. "
    "A finality notice establishes that a supplied plan is finalized, not that a user request "
    "has become an executed booking. Compound factual support is still checked by V.\n"
)


def source_view_v5(archive):
    view = compact_source(archive)
    view["typed_source_segments"] = [describe_source_event(event) for event in archive["events"] if event["event_type"] in SEGMENT_TYPES]
    return view


def messages_v5(stage, source, candidates, asset):
    result = messages_v4(stage, source, candidates, asset)
    result[0]["content"] += SEGMENT_NOTE
    data = json.loads(result[1]["content"])
    view = source_view_v5(source["archive"])
    data.update(view)
    data["source_view_id"] = fingerprint(view)
    result[1]["content"] = canonical(data).decode()
    return result


def resolve_v5(raw, archive):
    claims = resolve_v3(raw, archive, enforce_commit=False, reference_resolver=resolve_evidence)
    events = {event["event_id"]: event for event in archive["events"]}
    for claim in claims:
        if claim["content_type"] == "public_requirement":
            require(claim["status"] not in {"provided_final", "issued_plan", "committed"}, "A public requirement is not a plan or commit")
        if claim["status"] == "committed":
            require(any(events[ref["event_id"]]["event_type"] in archive.get("allowed_commit_event_types", [])
                        for ref in claim["source_refs"]), "Committed claim lacks explicit permitted receipt")
        if claim["status"] == "provided_final":
            admissible = False
            for ref in claim["source_refs"]:
                event = events[ref["event_id"]]
                if event["event_type"] in SEGMENT_TYPES:
                    descriptor = describe_source_event(event)
                    admissible |= has_explicit_supplied_plan_evidence(descriptor, ref["start"], ref["end"])
            require(admissible, "provided_final requires supplied-plan fields or explicit finality notice; query alone is insufficient")
        claim["source_evidence"] = [{"event_id": ref["event_id"], "span_ids": [line["span_id"]
                                      for line in evidence_lines(events[ref["event_id"]]) if line["start"] < ref["end"] and line["end"] > ref["start"]]}
                                    for ref in claim["source_refs"]]
    return claims


def full_delivery_v5(archive):
    view = source_view_v5(archive)
    return canonical({"archive_id": fingerprint(archive), "source_view_id": fingerprint(view), **view}).decode()
