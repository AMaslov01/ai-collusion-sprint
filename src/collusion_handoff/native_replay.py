"""Rebuild native state using cached responses and exact displayed tool results."""

from collections import deque
from copy import deepcopy
from pathlib import Path

import httpx

from .artifacts import atomic_json, fingerprint, read_json
from .checkpoint import actor_state, load_checkpoint, restore_actor, world_state
from .model_gateway import WireJournal, install_native_actor_gateway
from .smoke import journal_rows


class CapturedFirstRequest(BaseException):
    pass


class CachedTools:
    def __init__(self, events):
        self.remaining = deque(event for event in events if event["event"] == "tool_completed")

    def execute(self, name, arguments):
        if not self.remaining:
            raise RuntimeError("Cache replay requested an unrecorded tool action")
        receipt = self.remaining.popleft()
        if (receipt["tool_name"], receipt["arguments"]) != (name, arguments):
            raise RuntimeError("Cache replay tool arguments differ")
        return receipt["exact_displayed_result"]


def load_verified_result(work, relative, scientific_fingerprint):
    result = read_json(work / relative)
    if result["scientific_fingerprint"] != scientific_fingerprint:
        raise ValueError("Verified result belongs to a different scientific fingerprint")
    number = result["actor_task"]["round_idx"]
    manifest, _ = load_checkpoint(work / f"checkpoints/group-1/traveler-{number}", scientific_fingerprint)
    if manifest != result["checkpoint_manifest"]:
        raise ValueError("Verified result checkpoint identity mismatch")
    return result


def audit_first_group(config, work):
    from .native_adapter import install_data, make_agent, policy_for
    from .native_data import task_actor_view
    install_data(config)
    from env.env_systems.travel_env import TravelPlannerEnvironment

    work = Path(work)
    manifest = read_json(work / "manifest.json")
    science = manifest["scientific_fingerprint"]
    requests = journal_rows(work / "io/requests.jsonl")
    responses = journal_rows(work / "io/responses.jsonl")
    if not requests or len(requests) != len(responses):
        raise RuntimeError("Cache replay refuses unresolved attempts or incomplete IO")
    for request, response in zip(requests, responses):
        if response.get("status_code") != 200 or request["logical_call_id"] != response["logical_call_id"]:
            raise RuntimeError("Cache replay refuses transport failures or mismatched attempts")
        if request["request_sha256"] != response["request_sha256"]:
            raise RuntimeError("Cache replay request fingerprint mismatch")
    events = journal_rows(work / "events/group-1.jsonl")
    tools = CachedTools(events)
    environment = TravelPlannerEnvironment({"judgement_mode": "none"})
    observation = environment.reset(seed=1)
    agent = make_agent(config)
    original = agent.client
    replay_io = work / "replay-io"
    replay_io.mkdir(mode=0o700, exist_ok=True)
    _, initial = load_checkpoint(work / "checkpoints/group-1/traveler-0", science)
    restore_actor(agent, initial["actor"])
    agent.executor = tools
    cursor = 0

    def current_context():
        if cursor >= len(requests):
            raise RuntimeError("Cache replay tried to generate beyond recorded calls")
        return {"logical_call_id": requests[cursor]["logical_call_id"], "role": "A", "attempt": 1, "cache_only": True}

    def verify_request(payload):
        if fingerprint(payload) != requests[cursor]["request_sha256"]:
            raise RuntimeError(f"Cache replay request differs at {requests[cursor]['logical_call_id']}")

    def cached_model(request):
        nonlocal cursor
        response = responses[cursor]
        cursor += 1
        return httpx.Response(200, content=response["body_utf8"].encode(), headers={"content-type": "application/json"})

    install_native_actor_gateway(original, policy=policy_for(config), journal=WireJournal(replay_io),
        call_context=current_context, before_call=verify_request, timeout_seconds=900, inner=httpx.MockTransport(cached_model))
    checked = []
    try:
        for number in (1, 2, 3, 4):
            task = task_actor_view(observation, number)
            agent.prepare_for_person(name=task["name"], round_idx=number, include_previous_plans=True, memory_context=None, memory_system=None)
            action = agent.act(task["query"])
            expected = load_verified_result(work, Path(f"results/group-1-traveler-{number}.json"), science)
            if action != expected["final_text"]:
                raise RuntimeError("Cache replay final text differs")
            answer = next(item for item in observation["answers"] if item["round_idx"] == number)
            environment.step(action, ground_truth={"name": task["name"], "daily_plans": answer["daily_plans"], "judgement_mode": "none"}, need_judge=True)
            _, snapshot = load_checkpoint(work / f"checkpoints/group-1/traveler-{number}", science)
            if actor_state(agent) != snapshot["actor"] or world_state(environment) != snapshot["world"]:
                raise RuntimeError(f"Cache replay state differs after traveler {number}")
            checked.append(expected["job_id"])
        if cursor != len(requests) or tools.remaining:
            raise RuntimeError("Cache replay did not consume exactly the recorded interactions")
    finally:
        original.client.close()
        environment.close()

    # This second proof restores the persisted snapshot directly, independently
    # of the full replay above, and aborts in pre-send admission before transport.
    _, second = load_checkpoint(work / "checkpoints/group-1/traveler-2", science)
    target = next(row for row in requests if row["logical_call_id"] == "native/group-1/traveler-3/A/turn-1")
    from env.env_systems.travel_planner_env.clients.openai_client import OpenAIClient
    from .smoke import URL
    captured = {}
    client = OpenAIClient(model_name=config["model"]["served_alias"], api_key="local-inference", base_url=URL + "/v1")

    def capture(payload):
        captured["request_sha256"] = fingerprint(payload)
        raise CapturedFirstRequest()

    def forbidden(request):
        raise RuntimeError("Snapshot request reconstruction must never reach transport")

    install_native_actor_gateway(client, policy=policy_for(config), journal=WireJournal(replay_io),
        call_context=lambda: {"logical_call_id": target["logical_call_id"], "role": "A", "attempt": 1, "cache_only": True},
        before_call=capture, timeout_seconds=900, inner=httpx.MockTransport(forbidden))
    agent.client = client
    restore_actor(agent, second["actor"])
    task = task_actor_view({"questions": second["admin"]["group"]["questions"]}, 3)
    agent.prepare_for_person(name=task["name"], round_idx=3, include_previous_plans=True, memory_context=None, memory_system=None)
    try:
        try:
            agent.act(task["query"])
        except CapturedFirstRequest:
            pass
        else:
            raise RuntimeError("Snapshot reconstruction did not stop before dispatch")
    finally:
        client.client.close()
    if captured.get("request_sha256") != target["request_sha256"]:
        raise RuntimeError("Snapshot 2 did not reconstruct the actual first request 3")
    atomic_json(work / "audit.json", {"status": "passed", "scope": "native_dev_first_group",
        "model_calls_during_audit": 0, "cached_model_responses_consumed": cursor,
        "verified_jobs": checked, "snapshot_2_request_3_sha256": captured["request_sha256"],
        "scientific_fingerprint": science, "main_started": False})
