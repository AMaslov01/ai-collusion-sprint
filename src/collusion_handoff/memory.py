"""Frozen JSON, provenance and deletion-only memory transforms (no inference)."""

from copy import deepcopy
from hashlib import sha256
import json
import math

from jsonschema import Draft202012Validator

from .artifacts import canonical


class MemoryError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise MemoryError(message)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise MemoryError(f"Non-finite JSON value: {value}")

    def finite_float(value):
        parsed = float(value)
        require(math.isfinite(parsed), "Non-finite JSON number")
        return parsed

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite_float)


def validate(text, schema):
    value = strict_json(text)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    return value


def resolve_claims(raw, archive):
    """One invalid reference fails the whole extraction, before packing."""
    require(raw["claims"], "Empty extraction is a C package build failure")
    events = {event["event_id"]: event for event in archive["events"]}
    require(len(events) == len(archive["events"]), "Duplicate source event IDs")
    resolved, seen = [], set()
    for index, original in enumerate(raw["claims"]):
        claim = deepcopy(original)
        refs = []
        for ref in claim["source_refs"]:
            require(ref["event_id"] in events, f"Unknown source event: {ref['event_id']}")
            event = events[ref["event_id"]]
            content, quote = event["content"], ref["quote"]
            require(quote != "", "Empty provenance quote")
            start = content.find(quote)
            require(start >= 0 and content.find(quote, start + 1) < 0,
                    f"Quote absent or ambiguous: extraction index {index}, event {event['event_id']}")
            digest = sha256(content.encode()).hexdigest()
            require(digest == event["content_sha256"], "Source content hash mismatch")
            byte_start = len(content[:start].encode())
            byte_end = byte_start + len(quote.encode())
            require(content.encode()[byte_start:byte_end] == quote.encode(), "UTF-8 span mismatch")
            refs.append({"event_id": ref["event_id"], "start": byte_start, "end": byte_end, "source_hash": digest})
        claim["source_refs"] = refs
        identity = canonical(claim)
        if identity in seen:
            continue
        seen.add(identity)
        claim["claim_id"] = f"{archive['checkpoint_id']}/claim-{index}"
        claim["extraction_index"] = index
        claim["source_position"] = min([events[ref["event_id"]]["order"], ref["start"]] for ref in refs)
        resolved.append(claim)
    return sorted(resolved, key=lambda claim: (*claim["source_position"], claim["extraction_index"]))


def actor_record(claim):
    return {"text": claim["text"], "source_refs": deepcopy(claim["source_refs"])}


def claim_content(claims):
    return canonical([actor_record(claim) for claim in claims]).decode()


def pack_claims(claims, count_tokens, limit=2048):
    packed = []
    for claim in claims:
        if count_tokens(claim_content(packed + [claim])) > limit:
            break
        packed.append(claim)
    require(packed, "Zero whole C records fit the memory content budget")
    return packed, claims[len(packed):]


def pack_summary(sentences, count_tokens, limit=2048):
    packed = []
    for sentence in sentences:
        if count_tokens(" ".join(packed + [sentence])) > limit:
            break
        packed.append(sentence)
    require(packed, "Zero whole S sentences fit the memory content budget")
    return " ".join(packed), sentences[len(packed):]


def apply_verdicts(claims, verdicts, *, field, retain):
    expected = [claim["claim_id"] for claim in claims]
    actual = [verdict["claim_id"] for verdict in verdicts]
    require(len(actual) == len(set(actual)), "Duplicate verdict IDs")
    require(set(actual) == set(expected) and len(actual) == len(expected), "Missing or extra verdict IDs")
    by_id = {verdict["claim_id"]: verdict for verdict in verdicts}
    selected = [claim for claim in claims if by_id[claim["claim_id"]][field] == retain]
    errors = [identifier for identifier in expected if by_id[identifier][field] == "error"]
    # The exact actor record serialization must survive deletion unchanged.
    before = {claim["claim_id"]: canonical(actor_record(claim)) for claim in claims}
    require(all(canonical(actor_record(claim)) == before[claim["claim_id"]] for claim in selected), "Record rewritten")
    return selected, errors


def stage_messages(stage, archive, archive_sha, candidates, schema, prompt):
    data = {"stage": stage, "checkpoint_id": archive["checkpoint_id"], "archive_id": archive_sha,
            "source_policy_id": archive["source_policy_id"],
            "rubric_id": "native_transfer_policy_v1_proposed" if stage == "P" else None,
            "source_events": archive["events"], "candidates": candidates, "response_schema": schema}
    return [{"role": "system", "content": prompt}, {"role": "user", "content": canonical(data).decode()}]


def build_packages(source, contract, assets, generate, count_tokens, persist):
    """Single pass; each model stage is attempted at most once by the caller."""
    archive = source["archive"]
    packages = {}

    def package(arm, content, **metadata):
        value = {"arm": arm, "content": content, "content_sha256": sha256(content.encode()).hexdigest(),
                 "content_tokens": count_tokens(content),
                 "wrapped": contract["packing"]["archive_wrapper"].replace("{content}", content), **metadata}
        if arm in {"S", "C", "CV", "CVP"}:
            require(value["content_tokens"] <= 2048, f"{arm} memory exceeds content budget")
        packages[arm] = value
        persist(f"packages/{arm}.json", value)

    def call(stage, candidates):
        persist("stages/current.json", {"stage": stage, "status": "preparing_single_attempt"})
        asset = assets[stage]
        messages = stage_messages(stage, archive, source["archive_sha256"], candidates, asset["schema"], asset["prompt"])
        text = generate(stage, messages)
        value = validate(text, asset["schema"])
        persist(f"stages/{stage}.json", value)
        return value

    package("F", canonical(archive).decode(), status="built_full_permitted_archive")
    package("R", "", status="built_empty_private_archive")
    summary = call("S", [])
    summary_content, excluded_sentences = pack_summary(summary["sentences"], count_tokens)
    package("S", summary_content, status="built", excluded_by_budget_sentences=excluded_sentences)
    raw = call("C", [])
    claims = resolve_claims(raw, archive)
    packed, excluded = pack_claims(claims, count_tokens)
    persist("stages/C-resolved.json", {"raw_claims": len(raw["claims"]), "unique_claims": claims,
                                       "packed_claim_ids": [claim["claim_id"] for claim in packed],
                                       "excluded_by_budget": excluded})
    package("C", claim_content(packed), status="built", claim_ids=[claim["claim_id"] for claim in packed])
    verified = call("V", packed)
    cv, v_errors = apply_verdicts(packed, verified["verdicts"], field="verdict", retain="supported")
    package("CV", claim_content(cv), status="empty_after_deletion" if not cv else "built",
            claim_ids=[claim["claim_id"] for claim in cv], process_failure_claim_ids=v_errors)
    policy = call("P", cv) if cv else {"verdicts": []}
    if not cv:
        persist("stages/P-skipped.json", {"reason": "No CV candidates; deterministic empty table, no model call"})
    cvp, p_errors = apply_verdicts(cv, policy["verdicts"], field="label", retain="allow")
    package("CVP", claim_content(cvp), status="empty_after_deletion" if not cvp else "built",
            claim_ids=[claim["claim_id"] for claim in cvp], process_failure_claim_ids=p_errors)
    return {"package_counts": {arm: value.get("claim_ids") for arm, value in packages.items()},
            "raw_extraction_count": len(raw["claims"]), "unique_extraction_count": len(claims),
            "packed_C_count": len(packed), "CV_count": len(cv), "CVP_count": len(cvp),
            "excluded_by_budget_count": len(excluded), "V_error_count": len(v_errors), "P_error_count": len(p_errors)}
