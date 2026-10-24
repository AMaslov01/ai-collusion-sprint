"""Compare the adapter to actual pinned conversion code and check actor views."""

import ast
import json
import os
from pathlib import Path
import re
import unittest

from collusion_handoff.native_data import _convert_row, base_actor_view, task_actor_view


class NativeDataTests(unittest.TestCase):
    @unittest.skipUnless(Path("/tasks/data.jsonl").exists(), "Pinned-data integration requires the native-checks mount")
    def test_real_pinned_rows_match_upstream_conversion(self):
        source = Path(os.environ["MEMORYARENA_ROOT"]) / "env/env_systems/travel_planner_env/data_loader.py"
        tree = ast.parse(source.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"_extract_name", "_convert_row"}]
        self.assertEqual(len(functions), 2)
        namespace = {"re": re}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
        rows = [json.loads(line) for line in Path("/tasks/data.jsonl").read_text().splitlines() if line]
        self.assertEqual(len(rows), 270)
        for row in rows:
            self.assertEqual(_convert_row(row), namespace["_convert_row"](row))

    def test_actor_projection_excludes_gold_and_unissued_queries(self):
        observation = {
            "base_person": {"name": "base", "query": "base-query", "daily_plans": [], "gold": "BASE_ADMIN_SENTINEL"},
            "questions": [
                {"round_idx": 1, "name": "current", "query": "issued", "answer": "GOLD_SENTINEL"},
                {"round_idx": 2, "name": "future", "query": "FUTURE_QUERY_SENTINEL"},
            ],
            "answers": [{"secret": "GOLD_SENTINEL"}],
        }
        actor = {"base": base_actor_view(observation), "task": task_actor_view(observation, 1)}
        text = json.dumps(actor)
        for sentinel in ("BASE_ADMIN_SENTINEL", "GOLD_SENTINEL", "FUTURE_QUERY_SENTINEL"):
            self.assertNotIn(sentinel, text)
        actor["base"]["daily_plans"].append({"mutated": True})
        self.assertEqual(observation["base_person"]["daily_plans"], [])
