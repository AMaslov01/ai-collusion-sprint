"""Exact structural spans for the pinned native base-context template.

The descriptor separates explicit plan-finality notices, query text and supplied
plan fields. It does not establish plan compliance, bookings or execution.
Source bytes remain unchanged; an unsupported template is an explicit error.
"""

from hashlib import sha256
import re


class UnsupportedSourceFormat(ValueError):
    pass


KINDS = frozenset({"query_request", "explicit_finality_notice", "provided_plan_fields", "context"})
REQUEST_HEADER = re.compile(r"^=== (.+)'s Request \(Already Planned\) ===$")


def _require(condition, message):
    if not condition:
        raise UnsupportedSourceFormat(message)


def _lines(event):
    text = event["content"]
    digest = sha256(text.encode("utf-8")).hexdigest()
    _require(digest == event["content_sha256"], "Source content hash mismatch")
    result, start = [], 0
    for index, line in enumerate(text.splitlines(keepends=True), start=1):
        end = start + len(line.encode("utf-8"))
        result.append({"line_id": f"line-{index:04d}", "start": start, "end": end,
                       "text": line, "plain": line.rstrip("\r\n")})
        start = end
    _require(result and start == len(text.encode("utf-8")), "Empty or incomplete source lines")
    return result


def describe_source_event(event):
    """Return an exact partition, using authenticated event types and headers."""
    lines = _lines(event)
    event_type = event["event_type"]
    labels = ["context"] * len(lines)
    if event_type in {"base_query", "provided_base_plan"}:
        labels = ["query_request" if event_type == "base_query" else "provided_plan_fields"] * len(lines)
    elif event_type == "initial_given_base_context":
        headers = [(index, REQUEST_HEADER.fullmatch(line["plain"])) for index, line in enumerate(lines)]
        headers = [(index, match) for index, match in headers if match]
        _require(len(headers) == 1 and headers[0][0] == 0, "Unsupported native request header")
        name = headers[0][1].group(1)
        notice = f"{name} has already made their travel request and their plan has been finalized."
        notice_indices = [index for index, line in enumerate(lines) if line["plain"] == notice]
        query_indices = [index for index, line in enumerate(lines)
                         if line["plain"].startswith(name + "'s Query: ")]
        plan_indices = [index for index, line in enumerate(lines)
                        if line["plain"] == f"=== {name}'s Confirmed Plan ==="]
        end_indices = [index for index, line in enumerate(lines)
                       if line["plain"].startswith("Now other travelers are joining the trip.")]
        _require(all(len(items) == 1 for items in (notice_indices, query_indices, plan_indices, end_indices)),
                 "Native source boundaries are absent or ambiguous")
        notice_index, query_index, plan_index, end_index = (items[0] for items in
                                                         (notice_indices, query_indices, plan_indices, end_indices))
        _require(0 < notice_index < query_index < plan_index < end_index,
                 "Native source boundaries are out of order")
        _require(any(re.fullmatch(r"Day [1-9][0-9]*:", line["plain"])
                     for line in lines[plan_index:end_index]), "Confirmed plan body is unavailable")
        labels[notice_index] = "explicit_finality_notice"
        labels[query_index:plan_index] = ["query_request"] * (plan_index - query_index)
        labels[plan_index:end_index] = ["provided_plan_fields"] * (end_index - plan_index)
    else:
        raise UnsupportedSourceFormat(f"No base-context descriptor for {event_type}")

    segments = []
    for line, kind in zip(lines, labels):
        if not segments or segments[-1]["kind"] != kind:
            segments.append({"kind": kind, "event_id": event["event_id"],
                             "source_hash": event["content_sha256"], "start": line["start"],
                             "end": line["end"], "line_ids": [line["line_id"]]})
        else:
            segments[-1]["end"] = line["end"]
            segments[-1]["line_ids"].append(line["line_id"])
    return {"descriptor_version": "base_context_segments_v1", "event_id": event["event_id"],
            "source_hash": event["content_sha256"], "offset_unit": "UTF8_bytes",
            "segments": segments,
            "semantics": "Structural provenance only; no proof of compliance, booking or commit"}


def referenced_segments(descriptor, start, end):
    """Classify a valid original byte span; never rewrite or expand its refs."""
    segments = descriptor["segments"]
    _require(type(start) is int and type(end) is int and 0 <= start < end <= segments[-1]["end"],
             "Invalid byte span")
    return [segment for segment in segments if start < segment["end"] and end > segment["start"]]


def has_explicit_supplied_plan_evidence(descriptor, start, end):
    """Necessary status evidence; compound-claim support still belongs to V."""
    return any(segment["kind"] in {"explicit_finality_notice", "provided_plan_fields"}
               for segment in referenced_segments(descriptor, start, end))
