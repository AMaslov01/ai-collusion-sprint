"""Explicit native state, with actor/world/admin files committed together."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import os
from pathlib import Path
import uuid

from .artifacts import atomic_json, fingerprint, read_json


ACTOR_FIELDS = (
    "base_messages", "accumulated_plans", "base_name", "base_query", "all_queries",
    "previous_judgement", "system_prompt", "max_steps",
    "_pending_name", "_pending_round_idx", "_pending_include_previous_plans", "_pending_memory_context",
)


def actor_state(agent):
    if agent._pending_memory_system is not None:
        raise ValueError("Native history requires memory_system=none")
    return {"fields": deepcopy({field: getattr(agent, field) for field in ACTOR_FIELDS}),
            "last_result": asdict(agent.last_result) if agent.last_result else None}


def restore_actor(agent, state):
    if set(state["fields"]) != set(ACTOR_FIELDS):
        raise ValueError("Unrecognized actor-state schema")
    for field, value in state["fields"].items():
        setattr(agent, field, deepcopy(value))
    agent._pending_memory_system = None
    if state["last_result"] is not None:
        from agent.travel_planner import AgentResult, AgentStep
        result = deepcopy(state["last_result"])
        result["scratchpad"] = [AgentStep(**step) for step in result["scratchpad"]]
        agent._last_result = AgentResult(**result)
    else:
        agent._last_result = None


def world_state(environment):
    return deepcopy({"group_id": environment.current_group["id"], "history": environment.history,
                     "step_count": environment.step_count, "observation": environment._current_observation})


def save_checkpoint(root, *, agent, environment, group, next_round, scientific_fingerprint):
    root = Path(root)
    if root.exists():
        raise FileExistsError("Checkpoint already exists; do not overwrite or regenerate a verified job")
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = root.with_name(root.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.mkdir(mode=0o700)
    values = {"actor": actor_state(agent), "world": world_state(environment),
              "admin": {"group": deepcopy(group), "next_round_idx": next_round}}
    hashes = {}
    for name, value in values.items():
        path = temporary / f"{name}.json"
        atomic_json(path, value)
        hashes[name] = sha256(path.read_bytes()).hexdigest()
    manifest = {"schema": "native_checkpoint_v1", "scientific_fingerprint": scientific_fingerprint,
                "group_id": group["id"], "next_round_idx": next_round, "files": hashes,
                "actor_state_fingerprint": fingerprint(values["actor"]), "request_seed": 42}
    atomic_json(temporary / "manifest.json", manifest)
    load_checkpoint(temporary, scientific_fingerprint)
    os.rename(temporary, root)
    descriptor = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return manifest


def load_checkpoint(root, scientific_fingerprint):
    root = Path(root)
    manifest = read_json(root / "manifest.json")
    if manifest["schema"] != "native_checkpoint_v1" or manifest["scientific_fingerprint"] != scientific_fingerprint:
        raise ValueError("Checkpoint schema or scientific fingerprint mismatch")
    values = {}
    for name in ("actor", "world", "admin"):
        path = root / f"{name}.json"
        if sha256(path.read_bytes()).hexdigest() != manifest["files"][name]:
            raise ValueError(f"Corrupt checkpoint component: {name}")
        values[name] = read_json(path)
    if fingerprint(values["actor"]) != manifest["actor_state_fingerprint"]:
        raise ValueError("Actor state fingerprint mismatch")
    return manifest, values
