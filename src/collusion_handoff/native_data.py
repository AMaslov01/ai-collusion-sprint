"""Approved pinned-JSONL adapter for MemoryArena's data_loader interface.

The row conversion below preserves the pinned upstream implementation. Only
the network/datasets loader is replaced. The upstream checkout is unchanged.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from types import ModuleType


LOADER_MODULE = "env.env_systems.travel_planner_env.data_loader"


def _extract_name(query_str):
    match = re.match(r"I am (\w+)\.", query_str)
    return match.group(1) if match else "Person"


def _convert_row(row):
    questions = []
    for index, query_str in enumerate(row["questions"], start=1):
        questions.append({"round_idx": index, "name": _extract_name(query_str), "query": query_str})
    answers = []
    for index, daily_plans in enumerate(row["answers"], start=1):
        answers.append({"round_idx": index, "daily_plans": [dict(day) for day in daily_plans]})
    base = row["base_person"]
    return {
        "id": row["id"],
        "base_person": {"name": base["name"], "query": base["query"], "daily_plans": [dict(day) for day in base["daily_plans"]]},
        "questions": questions, "answers": answers,
    }


def install_pinned_loader(path: Path, expected_sha256: str):
    """Bind the existing load_travel_data API to a verified immutable local file."""
    content = path.read_bytes()
    if sha256(content).hexdigest() != expected_sha256:
        raise ValueError("Pinned task file checksum mismatch")
    existing = sys.modules.get(LOADER_MODULE)
    if existing is not None:
        if getattr(existing, "pinned_sha256", None) != expected_sha256:
            raise RuntimeError("A different data loader has already been imported")
        return existing
    rows = [_convert_row(json.loads(line)) for line in content.decode("utf-8").splitlines() if line]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate group ID")
    module = ModuleType(LOADER_MODULE, __doc__)
    module.__file__ = __file__
    module.DATASET_NAME = "ZexueHe/memoryarena"
    module.DATASET_CONFIG = "group_travel_planner"
    module.pinned_sha256 = expected_sha256
    module._extract_name = _extract_name
    module._convert_row = _convert_row
    module.load_travel_data = lambda: deepcopy(rows)
    sys.modules[LOADER_MODULE] = module
    return module


def base_actor_view(observation):
    base = observation["base_person"]
    return deepcopy({key: base[key] for key in ("name", "query", "daily_plans")})


def task_actor_view(observation, round_idx):
    matching = [item for item in observation["questions"] if item["round_idx"] == round_idx]
    if len(matching) != 1:
        raise ValueError("Current traveler identity is not unique")
    task = matching[0]
    return deepcopy({key: task[key] for key in ("round_idx", "name", "query")})
