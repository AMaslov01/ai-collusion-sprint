"""Cache-only verification of v3 wire accounting and deterministic transforms."""

from hashlib import sha256
from pathlib import Path
import traceback

import httpx

from .artifacts import atomic_json, fingerprint, read_json
from .cycle_budget import CycleBudget
from .memory import apply_verdicts, build_packages, require, stage_messages, strict_json
from .memory_v3 import decoder_v3, fixture_source, full_delivery, resolve_v31, messages_v31, fixture_eligible
from .repair_cycle import BASE_CONFIG, CODE, ROOT, WORK, content_of, payload_for, replay_native, settings
from .memory_runner import tokenizer


COMPLETED = Path("/completed")


def rows(path):
    data = path.read_bytes() if path.exists() else b""
    require(not data or data.endswith(b"\n"), "Truncated cache tail; do not infer zero spend")
    return [strict_json(line) for line in data.decode().splitlines()]


def replay_memory(job, contract, assets, tokens):
    source = read_json(job / "source.json")
    requests, responses = rows(job / "io/requests.jsonl"), rows(job / "io/responses.jsonl")
    require(len(requests) == len(responses), "Unresolved memory attempt")
    cursor, replayed = 0, {}

    def cached(stage, messages):
        nonlocal cursor
        require(cursor < len(requests), "No cached next attempt; preserved admission failure boundary")
        request, response = requests[cursor], responses[cursor]
        cursor += 1
        expected = payload_for(stage, messages, contract)
        require(strict_json(request["body_utf8"]) == expected, "Memory replay wire differs")
        require(response.get("status_code") == 200, "Cached HTTP/transport failure")
        return content_of(httpx.Response(200, content=response["body_utf8"].encode()))

    failure = None
    try:
        build_packages(source, contract, assets, cached, lambda text: len(tokens.encode(text, add_special_tokens=False)),
                       replayed.__setitem__, decode=decoder_v3(replayed.__setitem__), resolve=resolve_v31,
                       full_archive_content=full_delivery, message_builder=messages_v31)
    except Exception as error:
        failure = {"type": type(error).__name__, "message": str(error)}
    actual = read_json(job / "status.json")
    require((failure is None) == (actual["status"] == "built"), "Memory replay success/failure differs")
    require(cursor == len(requests), "Replay did not consume exact memory calls")
    for name, value in replayed.items():
        if name != "stages/current.json":
            require(read_json(job / "automatic-withheld" / name) == value, "Memory replay artifact differs: " + name)
    return {"status": "passed_cached_success" if failure is None else "passed_completed_prefix_with_failure", "cached_calls": cursor,
            "replayed_artifacts": len(replayed), "preserved_failure_boundary": failure, "new_model_calls": 0}


def replay_fixture(definition, job, contract, assets):
    source, candidates = fixture_source(definition)
    require(source == read_json(job / "source.json") and candidates == read_json(job / "candidates.json"), "Fixture source/candidates changed")
    requests, responses = rows(job / "io/requests.jsonl"), rows(job / "io/responses.jsonl")
    require(len(requests) == len(responses), "Unresolved fixture request")
    cursor, replayed = 0, {}
    decode = decoder_v3(replayed.__setitem__)

    def cached(stage, claims):
        nonlocal cursor
        require(cursor < len(requests), "Fixture admission failure preserved")
        request, response = requests[cursor], responses[cursor]
        require(request["logical_call_id"].endswith("/" + stage), "Unrecorded component admission failure")
        cursor += 1
        messages = messages_v31(stage, source, claims, assets[stage])
        require(strict_json(request["body_utf8"]) == payload_for(stage, messages, contract), "Fixture replay wire differs")
        require(response.get("status_code") == 200, "Cached fixture transport/HTTP failure")
        value = decode(stage, content_of(httpx.Response(200, content=response["body_utf8"].encode())), assets[stage]["schema"])
        replayed[f"stages/{stage}.json"] = value
        return value

    failures = {}
    try:
        verification = cached("V", candidates)
        supported, _ = apply_verdicts(candidates, verification["verdicts"], field="verdict", retain="supported")
    except Exception as error:
        failures["V"] = {"type": type(error).__name__, "message": str(error)}
    eligible = fixture_eligible(definition, candidates)
    try:
        policy = cached("P", eligible)
        apply_verdicts(eligible, policy["verdicts"], field="label", retain="allow")
    except Exception as error:
        failures["P"] = {"type": type(error).__name__, "message": str(error)}
    actual = read_json(job / "status.json")
    require((not failures) == (actual["status"] == "complete"), "Fixture replay success/failure differs")
    require(set(failures) == set(actual.get("component_failures", {})), "Fixture failed components differ")
    require(cursor == len(requests), "Fixture replay did not consume exact cache")
    for name, value in replayed.items():
        require(read_json(job / "automatic-withheld" / name) == value, "Fixture replay artifact differs")
    return {"status": "passed_cache_only", "cached_calls": cursor, "preserved_component_failures": failures, "new_model_calls": 0}


def main():
    config, base, contract, _, definitions = settings()
    phase = read_json(COMPLETED / "phase-manifest.json")
    assets = read_json(COMPLETED / "assets.json")
    require(fingerprint(assets) == phase["assets_sha256"], "Frozen assets changed")
    require(sha256((CODE / "fixtures/memory-semantic-v3.json").read_bytes()).hexdigest() == phase["fixture_file_sha256"], "Frozen fixtures changed")
    requests, responses = rows(ROOT / "ledger/requests.jsonl"), rows(ROOT / "ledger/responses.jsonl")
    require(len({item["logical_call_id"] for item in requests}) == len(requests) <= 80, "Global attempt identity/count violation")
    response_map = {item["logical_call_id"]: item for item in responses}
    require(len(response_map) == len(responses), "Duplicate response receipt")
    for request in requests:
        require(sha256(request["body_utf8"].encode()).hexdigest() == request["request_sha256"] and request["attempt"] == 1, "Actual wire hash/attempt changed")
        if request["logical_call_id"] in response_map:
            require(response_map[request["logical_call_id"]]["request_sha256"] == request["request_sha256"], "Response refers to another request")
    with httpx.Client(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(RuntimeError("Audit forbids inference")))) as http:
        state = CycleBudget(ROOT, config, http, phase_work=WORK).state()
    require(state["accounted_input_output_tokens"] <= 3000000, "Token budget exceeded")
    require(all(count <= config["allocations"][key] for key, count in state["allocations"].items()), "Allocation exceeded")
    tokens = tokenizer()
    results = {}
    memory_jobs = [COMPLETED / "memory"] if (COMPLETED / "memory/manifest.json").exists() else sorted((COMPLETED / "memory").glob("group-*"))
    for job in memory_jobs:
        results[str(job.relative_to(COMPLETED))] = replay_memory(job, contract, assets, tokens)
    for group_id in (2, 3):
        job = COMPLETED / f"native/group-{group_id}"
        if (job / "status.json").exists() and read_json(job / "status.json")["status"] == "complete":
            replay_native(job, group_id, base, output=WORK / f"native/group-{group_id}")
            results[f"native/group-{group_id}"] = read_json(WORK / f"native/group-{group_id}/audit.json")
    for definition in definitions["fixtures"]:
        job = COMPLETED / "fixtures" / definition["fixture_id"]
        if (job / "status.json").exists():
            results["fixtures/" + definition["fixture_id"]] = replay_fixture(definition, job, contract, assets)
    atomic_json(WORK / "audit.json", {"status": "passed_cache_only", "phase": phase["phase"], "new_model_calls": 0,
                "global_budget": state, "jobs": results, "human_gold": False, "main_started": False})


if __name__ == "__main__":
    main()
