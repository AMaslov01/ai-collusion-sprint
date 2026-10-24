"""Build the approved group1/cp2 source from actual delivered native messages."""

import ast
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .artifacts import canonical, fingerprint, read_json
from .checkpoint import load_checkpoint
from .smoke import journal_rows


class SourceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise SourceError(message)


def prompt_literals(upstream):
    path = Path(upstream) / "env/env_systems/travel_planner_env/prompts.py"
    wanted = {"AGENT_USER_PROMPT_TEMPLATE", "HISTORY_TEMPLATE"}
    values = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    values[target.id] = ast.literal_eval(node.value)
    require(set(values) == wanted and all(isinstance(value, str) for value in values.values()), "Pinned prompt literals not found")
    return values, sha256(path.read_bytes()).hexdigest()


def build_source(native, upstream):
    native = Path(native)
    manifest = read_json(native / "manifest.json")
    science = manifest["scientific_fingerprint"]
    checkpoints = [load_checkpoint(native / f"checkpoints/group-1/traveler-{number}", science)[1] for number in (0, 1, 2)]
    initial, first, second = [value["actor"]["fields"] for value in checkpoints]
    group = checkpoints[2]["admin"]["group"]
    require(group["id"] == 1, "Expected native group1")
    questions = {item["round_idx"]: item for item in group["questions"] if item["round_idx"] in (1, 2)}
    require(set(questions) == {1, 2}, "Expected exactly the first two issued traveler identities")
    results = {number: read_json(native / f"results/group-1-traveler-{number}.json") for number in (1, 2)}
    templates, prompt_sha = prompt_literals(upstream)
    require(len(initial["base_messages"]) == 2, "Unrecognized native base-message layout")
    require(initial["base_messages"][0]["role"] == "system" and initial["base_messages"][1]["role"] == "user", "Unrecognized base roles")
    require(initial["all_queries"] == [f"{initial['base_name']}: {initial['base_query']}"], "Initial query state differs")
    for number, prior, current in ((1, initial, first), (2, first, second)):
        task = questions[number]
        require(current["all_queries"] == prior["all_queries"] + [f"{task['name']}: {task['query']}"], "Issued-query origin mapping failed")
        require(current["accumulated_plans"] == prior["accumulated_plans"] + "\n\n" + results[number]["final_text"], "Plan-history origin mapping failed")
        require(current["base_messages"] == initial["base_messages"] and current["previous_judgement"] == "", "Unexpected base or judge-state change")
    request_rows = journal_rows(native / "io/requests.jsonl")
    response_rows = journal_rows(native / "io/responses.jsonl")
    response_by_id = {row["logical_call_id"]: row for row in response_rows}
    selected = []
    for row in request_rows:
        parts = row["logical_call_id"].split("/")
        if len(parts) == 5 and parts[:2] == ["native", "group-1"] and parts[2] in {"traveler-1", "traveler-2"}:
            selected.append(row)
    require(selected, "No recorded prefix requests")
    events = {}
    call_origins = {}
    events_log = journal_rows(native / "events/group-1.jsonl")
    receipts = {(event["logical_call_id"], event["tool_call_id"]): event for event in events_log if event["event"] == "tool_completed"}

    def add(event_id, role, kind, content, number, turn, evidence, derived=()):
        require(isinstance(content, str), "Source content must be exact text")
        event = events.get(event_id)
        if event is None:
            event = {"event_id": event_id, "origin_event_id": event_id, "role": role, "event_type": kind,
                     "group_id": 1, "traveler_idx": number, "turn_idx": turn, "content": content,
                     "content_sha256": sha256(content.encode()).hexdigest(),
                     "source_version": {"native_scientific_fingerprint": science,
                                        "upstream_commit": manifest["scientific"]["upstream_commit"],
                                        "catalog_identity_sha256": manifest["scientific"]["catalog_identity_sha256"]},
                     "first_delivery": deepcopy(evidence), "delivery_refs": [], "derived_from": list(derived)}
            events[event_id] = event
        require((event["content"], event["role"], event["event_type"]) == (content, role, kind), "One logical origin changed without a new version")
        event["delivery_refs"].append(deepcopy(evidence))
        return event

    final_ids = {}
    for row in selected:
        logical = row["logical_call_id"]
        parts = logical.split("/")
        number, turn = int(parts[2].split("-")[1]), int(parts[4].split("-")[1])
        payload = json.loads(row["body_utf8"])
        require(fingerprint(payload) == row["request_sha256"], "Recorded request hash mismatch")
        messages = payload["messages"]
        prior = initial if number == 1 else first
        task = questions[number]
        expected = deepcopy(initial["base_messages"])
        if number == 2:
            history = templates["HISTORY_TEMPLATE"].format(
                all_queries="\n".join(prior["all_queries"] + [f"{task['name']}: {task['query']}"]),
                previous_plan=prior["accumulated_plans"], judgement="")
            expected.append({"role": "user", "content": history})
        expected.append({"role": "user", "content": templates["AGENT_USER_PROMPT_TEMPLATE"].format(name=task["name"], query=task["query"])})
        require(messages[:len(expected)] == expected, "Actual native input prefix differs from proven origins")
        for index, message in enumerate(messages):
            evidence = {"logical_call_id": logical, "request_sha256": row["request_sha256"], "timestamp": row["timestamp"], "message_index": index}
            if index == 0:
                continue  # Current system role text is excluded, never replayed to a builder.
            if index == 1:
                add("g1/base-context", "user", "initial_given_base_context", message["content"], 0, 0, evidence)
            elif index < len(expected):
                if number == 2 and index == 2:
                    require(1 in final_ids, "Missing prior final-plan origin")
                    add("g1/t2/history", "user", "delivered_native_history_context", message["content"], 2, 1, evidence,
                        ["g1/base-context", "g1/t1/query", final_ids[1], "g1/t2/query"])
                else:
                    add(f"g1/t{number}/query", "user", "issued_traveler_messages", message["content"], number, 1, evidence)
            elif message["role"] == "assistant":
                require(message.get("content") is None and message.get("tool_calls"), "Unexpected appended assistant message")
                for call in message["tool_calls"]:
                    key = (number, call["id"])
                    require(key in call_origins, "Tool delivery has no unique created origin")
                    event_id, created_logical, created_turn = call_origins[key]
                    text = canonical({"name": call["function"]["name"], "arguments": json.loads(call["function"]["arguments"])}).decode()
                    add(event_id, "A", "tool_calls", text, number, created_turn, {**evidence, "tool_call_id": call["id"]})
            elif message["role"] == "tool":
                key = (number, message["tool_call_id"])
                require(key in call_origins, "Tool result has no unique call origin")
                event_id, created_logical, created_turn = call_origins[key]
                receipt = receipts[(created_logical, message["tool_call_id"])]
                require(message["content"] == receipt["exact_displayed_result"], "Delivered tool text differs from receipt")
                add(event_id + "/result", "tool", "exact_delivered_tool_results", message["content"], number, created_turn,
                    {**evidence, "tool_call_id": message["tool_call_id"]})
            else:
                raise SourceError("Unmapped appended native message")
        response = response_by_id[logical]
        require(response.get("status_code") == 200 and response["request_sha256"] == row["request_sha256"], "Prefix response is unresolved")
        raw = json.loads(response["body_utf8"])
        message = raw["choices"][0]["message"]
        evidence = {"logical_call_id": logical, "response_id": raw["id"], "timestamp": response["timestamp"], "field": "content", "source_subindex": 0}
        if message.get("content"):
            final = not message.get("tool_calls")
            event_id = f"g1/t{number}/turn{turn}/content"
            add(event_id, "A", "final_plans" if final else "visible_assistant_content", message["content"], number, turn, evidence)
            if final:
                require(message["content"] == results[number]["final_text"], "Final text source mismatch")
                final_ids[number] = event_id
        for call_index, call in enumerate(message.get("tool_calls") or [], 1):
            key = (number, call["id"])
            require(key not in call_origins, "Reused native tool ID makes origin ambiguous")
            event_id = f"g1/t{number}/turn{turn}/tool/{call['id']}"
            call_origins[key] = (event_id, logical, turn)
            text = canonical({"name": call["function"]["name"], "arguments": json.loads(call["function"]["arguments"])}).decode()
            add(event_id, "A", "tool_calls", text, number, turn, {**evidence, "field": "tool_calls", "tool_call_id": call["id"], "source_subindex": call_index})
    require(set(final_ids) == {1, 2}, "Prefix is not two completed native travelers")
    for event in events.values():
        require(all(origin in events for origin in event["derived_from"]), "Unmapped derived-history origin")
    ordered = sorted(events.values(), key=lambda event: (event["first_delivery"]["timestamp"],
        event["first_delivery"].get("message_index", 1000000), event["first_delivery"].get("source_subindex", 0), event["event_id"]))
    for index, event in enumerate(ordered):
        event["order"] = index
    archive = {"schema_version": "handoff_source_group1_cp2_v1", "checkpoint_id": "group1-after-traveler2",
               "source_policy_id": "native_memory_group1_fcab0ad", "events": ordered}
    return {"archive": archive, "archive_sha256": fingerprint(archive), "native_scientific_fingerprint": science,
            "native_prompt_file_sha256": prompt_sha, "prefix_model_attempts": len(selected),
            "excluded": ["system_role_text", "raw_reasoning_fields", "hidden_answers", "future_queries", "travelers_after2"],
            "checkpoint2_manifest_sha256": sha256((native / "checkpoints/group-1/traveler-2/manifest.json").read_bytes()).hexdigest()}
