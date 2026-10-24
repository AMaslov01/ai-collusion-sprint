"""Lossless source tables and actor provenance; no selection or semantic changes."""

from copy import deepcopy
import json

from .artifacts import canonical
from .memory import actor_record, require
from .memory_v3 import evidence_view


REF_FIELDS = {"event_id", "source_hash", "start", "end"}


class ProvenanceCodec:
    """One shared dictionary, frozen for C and both deletion-only descendants."""

    def __init__(self, claims, source_order):
        self.sources = []
        self.indices = {}
        require(len(source_order) == len(set(source_order)), "Duplicate source identity")
        aliases = {event_id: index for index, event_id in enumerate(source_order)}
        for claim in claims:
            for ref in claim["source_refs"]:
                require(set(ref) == REF_FIELDS, "Unrecognized provenance fields")
                identity = (ref["event_id"], ref["source_hash"])
                if identity not in self.indices:
                    require(not any(event == identity[0] for _, event, _ in self.sources), "One source has conflicting hashes")
                    require(identity[0] in aliases, "Unknown source identity")
                    self.indices[identity] = aliases[identity[0]]
                    self.sources.append([aliases[identity[0]], *identity])
        self.sources.sort(key=lambda item: item[0])

    def content(self, claims):
        records = []
        for claim in claims:
            refs = []
            for ref in claim["source_refs"]:
                require(set(ref) == REF_FIELDS, "Unrecognized provenance fields")
                index = self.indices[(ref["event_id"], ref["source_hash"])]
                refs.append([index, ref["start"], ref["end"]])
            records.append({"text": claim["text"], "source_refs": refs})
        envelope = {"format": "provenance_dictionary_v1", "sources": self.sources, "records": records}
        require(self.decode(envelope) == [actor_record(claim) for claim in claims], "Actor provenance roundtrip failed")
        return canonical(envelope).decode()

    @staticmethod
    def decode(value):
        require(value["format"] == "provenance_dictionary_v1", "Unknown actor serialization")
        sources = {index: (event_id, digest) for index, event_id, digest in value["sources"]}
        require(len(sources) == len(value["sources"]), "Duplicate source dictionary alias")
        records = []
        for record in value["records"]:
            refs = []
            for index, start, end in record["source_refs"]:
                require(type(index) is int and index in sources, "Invalid source dictionary index")
                event_id, source_hash = sources[index]
                refs.append({"event_id": event_id, "source_hash": source_hash, "start": start, "end": end})
            records.append({"text": record["text"], "source_refs": refs})
        return records


def pack_provenance_prefix(claims, count_tokens, source_order, limit=2048):
    """Keep the existing chronological whole-record prefix; no backfill."""
    packed = []
    for claim in claims:
        candidate = packed + [claim]
        if count_tokens(ProvenanceCodec(candidate, source_order).content(candidate)) > limit:
            break
        packed = candidate
    require(packed, "Zero whole C records fit the memory content budget")
    return packed, claims[len(packed):], ProvenanceCodec(packed, source_order)


def compact_source(archive):
    original = evidence_view(archive)
    events = original["source_events"]
    require(events, "Empty source archive")
    columns = list(events[0])
    require(all(set(event) == set(columns) for event in events), "Event projection schemas differ")
    self_origin = all(event["origin_event_id"] == event["event_id"] for event in events)
    if self_origin:
        columns.remove("origin_event_id")
    rows = []
    for event in events:
        row = []
        for key in columns:
            value = event[key]
            if key == "evidence_lines":
                value = [[line["span_id"], line["text"]] for line in value]
            row.append(deepcopy(value))
        rows.append(row)
    value = {key: deepcopy(item) for key, item in original.items() if key != "source_events"}
    value.update({"serialization": "lossless_event_table_v2", "event_columns": columns, "event_rows": rows,
                  "event_derivations": {"origin_event_id": "event_id"} if self_origin else {}})
    require(expand_source(value) == original, "Source presentation roundtrip failed")
    return value


def expand_source(value):
    require(value["serialization"] == "lossless_event_table_v2", "Unknown source serialization")
    columns = value["event_columns"]
    require(len(columns) == len(set(columns)), "Duplicate event column")
    original = {key: deepcopy(item) for key, item in value.items() if key not in {"serialization", "event_columns", "event_rows", "event_derivations"}}
    events = []
    for row in value["event_rows"]:
        require(len(row) == len(columns), "Invalid event table row")
        event = dict(zip(columns, deepcopy(row)))
        for target, source in value["event_derivations"].items():
            event[target] = deepcopy(event[source])
        event["evidence_lines"] = [{"span_id": span, "text": text} for span, text in event["evidence_lines"]]
        events.append(event)
    original["source_events"] = events
    return original


def compact_candidates(claims, archive, *, shared_source=False):
    if not claims:
        return {"format": "lossless_candidate_table_v1", "columns": [], "rows": [], "sources": []}
    order = [event["event_id"] for event in sorted(archive["events"], key=lambda item: item["order"])]
    codec = ProvenanceCodec(claims, order)
    columns = list(claims[0])
    require(all(set(item) == set(columns) for item in claims), "Candidate schemas differ")
    aliases = {event: index for index, event in enumerate(order)}
    rows = []
    for claim in claims:
        encoded = deepcopy(claim)
        encoded["source_refs"] = json.loads(codec.content([claim]))["records"][0]["source_refs"]
        if "source_roles" in encoded:
            encoded["source_roles"] = [[aliases[item["event_id"]], item["role"], item["event_type"]] for item in claim["source_roles"]]
        if "source_evidence" in encoded:
            encoded["source_evidence"] = [[aliases[item["event_id"]], item["span_ids"]] for item in claim["source_evidence"]]
        rows.append([encoded[key] for key in columns])
    value = {"format": "lossless_candidate_table_v1", "columns": columns, "rows": rows, "sources": codec.sources}
    require(expand_candidates(value) == claims, "Candidate roundtrip failed")
    if shared_source:
        value.pop("sources")
        value["source_table"] = "same_request_source_events_by_order"
        require(expand_candidates(value, archive) == claims, "Shared source roundtrip failed")
    return value


def expand_candidates(value, archive=None):
    require(value["format"] == "lossless_candidate_table_v1", "Unknown candidate serialization")
    if "source_table" in value:
        require(archive is not None, "Shared source candidate table needs this request's source archive")
        source_rows = [[index, event["event_id"], event["content_sha256"]]
                       for index, event in enumerate(sorted(archive["events"], key=lambda item: item["order"]))]
    else:
        source_rows = value["sources"]
    sources = {alias: (event, digest) for alias, event, digest in source_rows}
    result = []
    for row in value["rows"]:
        require(len(row) == len(value["columns"]), "Invalid candidate table row")
        claim = dict(zip(value["columns"], deepcopy(row)))
        envelope = {"format": "provenance_dictionary_v1", "sources": source_rows,
                    "records": [{"text": claim["text"], "source_refs": claim["source_refs"]}]}
        claim["source_refs"] = ProvenanceCodec.decode(envelope)[0]["source_refs"]
        if "source_roles" in claim:
            claim["source_roles"] = [{"event_id": sources[alias][0], "role": role, "event_type": kind} for alias, role, kind in claim["source_roles"]]
        if "source_evidence" in claim:
            claim["source_evidence"] = [{"event_id": sources[alias][0], "span_ids": spans} for alias, spans in claim["source_evidence"]]
        result.append(claim)
    return result
