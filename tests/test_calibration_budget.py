"""Synthetic append-only parent ledger and stricter calibration subceilings."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from collusion_handoff.abe_cycle import namespace
from collusion_handoff.artifacts import append_event, atomic_json
from collusion_handoff.calibration_budget import CalibrationBudget, journal, verify_parent
from collusion_handoff.cycle_budget import BudgetExceeded
from collusion_handoff.memory import MemoryError


class CalibrationBudgetTests(unittest.TestCase):
    def fixture(self, root, *, phase="synthetic-prefix", amendment_change=None):
        limits = {"absolute_cutoff_utc": "2099-01-01T00:00:00Z", "gpu_service_accounting": "sum_allocated_container_lifetimes",
                  "max_new_model_attempts": 400, "max_input_output_tokens": 6000000, "gpu_service_wall_seconds": 14400,
                  "task_max_calls": 10}
        profile = {"limits": limits, "allocations": {"validation_actors": 280, "reviewed_targeted_repairs": 22}}
        atomic_json(root / "master-manifest.json", profile)
        append_event(root / "ledger/requests.jsonl", {"logical_call_id": "synthetic-old", "allocation": "validation_actors",
                                                     "reserved_input_output_tokens": 5})
        append_event(root / "ledger/responses.jsonl", {"logical_call_id": "synthetic-old", "accounted_actual_tokens": 5})
        closure = {"status": "closed_model_calls_and_cache_checks", "main_allowed": False,
                   "master_manifest_sha256": sha256((root / "master-manifest.json").read_bytes()).hexdigest(),
                   "limits": limits, "allocations": profile["allocations"],
                   "before": {"attempts": 1, "input_output_tokens": 5, "allocated_gpu_container_seconds": 1,
                              "unknown_spend_requests": []},
                   "remaining_allocations": {"validation_actors": 279, "reviewed_targeted_repairs": 22},
                   "ledger_prefix": {name: {"sha256": sha256((root / "ledger" / name).read_bytes()).hexdigest(),
                                              "bytes": (root / "ledger" / name).stat().st_size, "rows": 1}
                                     for name in ("requests.jsonl", "responses.jsonl")}}
        amendment = {"run_id": "synthetic-calibration", "phases": {"prefix": "synthetic-prefix", "handoff": "synthetic-handoff"},
                     "source_frame": {"ordered_ids": list(range(8, 20))}, "arms": ["F", "S", "C", "CV", "CVP", "R"],
                     "charges": {"prefix_actors": "validation_actors", "memory_builders": "reviewed_targeted_repairs",
                                 "continuation_actors": "validation_actors"},
                     "child_limits": {"model_attempts": 164, "input_output_tokens": 2000000, "gpu_container_seconds": 5400,
                                      "prefix_actor_calls": 40, "memory_builder_calls": 4, "continuation_actor_calls": 120}}
        if amendment_change:
            amendment_change(amendment)
        (root / "gpu-service-deadline.unix").write_text("10000")
        generation_calls = []
        def response(request):
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"count": 3, "max_model_len": 32768})
            generation_calls.append(request)
            return httpx.Response(200, json={"usage": {"prompt_tokens": 3, "completion_tokens": 2}})
        http = httpx.Client(transport=httpx.MockTransport(response))
        self.addCleanup(http.close)
        work = root / "work"
        work.mkdir()
        budget = CalibrationBudget(root, profile, http, phase_work=work, amendment=amendment, closure=closure,
                                   phase=phase, selected_worlds=[8, 9], handoff_world=8 if phase.endswith("handoff") else None,
                                   clock=lambda: 100)
        return budget, profile, closure, amendment, generation_calls

    def identity(self, group=8, task=1, call=1, *, recipient=None):
        role = "prefix" if recipient is None else "recipient/" + recipient
        return f"{namespace('synthetic-calibration', group, role)}/g{group}-t{task}/A/cycle-1/call-{call}"

    def payload(self):
        return {"model": "synthetic", "messages": [], "max_tokens": 40}

    def test_old_bytes_and_global_master_survive_one_globally_charged_child_call(self):
        with TemporaryDirectory() as temporary, patch("collusion_handoff.cycle_budget.admission"):
            root = Path(temporary)
            budget, profile, closure, amendment, calls = self.fixture(root)
            master = (root / "master-manifest.json").read_bytes()
            old = (root / "ledger/requests.jsonl").read_bytes()
            budget.dispatch(self.payload(), self.identity(), "validation_actors")
            self.assertEqual(len(calls), 1)
            self.assertEqual(budget.state()["attempts"], 2)
            self.assertEqual(budget.state()["allocations"]["validation_actors"], 2)
            self.assertEqual(budget.child_state()["counts"], {"prefix_actors": 1})
            self.assertEqual(budget.child_state()["input_output_tokens"], 5)
            self.assertEqual((root / "master-manifest.json").read_bytes(), master)
            self.assertTrue((root / "ledger/requests.jsonl").read_bytes().startswith(old))
            verify_parent(root, closure, profile)
            with self.assertRaises(MemoryError):
                budget.dispatch(self.payload(), self.identity(), "validation_actors")
            self.assertEqual(len(calls), 1)

    def test_parent_mutation_wrong_world_alias_or_charge_cannot_generate(self):
        with TemporaryDirectory() as temporary, patch("collusion_handoff.cycle_budget.admission"):
            root = Path(temporary)
            budget, profile, closure, amendment, calls = self.fixture(root)
            for identity, charge in ((self.identity(group=10), "validation_actors"), (self.identity(call=11), "validation_actors"),
                                     (self.identity(), "reviewed_targeted_repairs")):
                with self.assertRaises(MemoryError):
                    budget.dispatch(self.payload(), identity, charge)
            self.assertEqual(calls, [])
            path = root / "ledger/requests.jsonl"
            path.write_bytes(path.read_bytes().replace(b"synthetic-old", b"synthetic-bad"))
            with self.assertRaises(MemoryError):
                budget.child_state()

    def test_child_attempt_ceiling_does_not_reset_the_parent_or_get_an_extra_call(self):
        def change(value):
            value["child_limits"].update(prefix_actor_calls=1, model_attempts=125)
        with TemporaryDirectory() as temporary, patch("collusion_handoff.cycle_budget.admission"):
            budget, _, _, _, calls = self.fixture(Path(temporary), amendment_change=change)
            budget.dispatch(self.payload(), self.identity(), "validation_actors")
            with self.assertRaises(BudgetExceeded):
                budget.dispatch(self.payload(), self.identity(call=2), "validation_actors")
            self.assertEqual(len(calls), 1)
            self.assertEqual(budget.state()["attempts"], 2)

    def test_child_token_reservation_blocks_before_generation_without_unknown_spend(self):
        with TemporaryDirectory() as temporary, patch("collusion_handoff.cycle_budget.admission"):
            budget, _, _, _, calls = self.fixture(Path(temporary), amendment_change=lambda value:
                                                   value["child_limits"].update(input_output_tokens=1))
            with self.assertRaises(BudgetExceeded):
                budget.dispatch(self.payload(), self.identity(), "validation_actors")
            self.assertEqual(calls, [])
            self.assertEqual(budget.state()["attempts"], 1)
            self.assertEqual(budget.state()["unknown_spend_requests"], [])
            self.assertIn(self.identity(), budget.child_state()["claimed_logical_ids"])

    def test_unclaimed_global_post_parent_call_or_unselected_memory_source_fails_closed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            budget, _, _, _, calls = self.fixture(root, phase="synthetic-handoff")
            self.assertEqual(budget.classify("synthetic-handoff/group-8/C", "reviewed_targeted_repairs", "synthetic-handoff"), "memory_builders")
            self.assertEqual(budget.classify(self.identity(task=3, recipient="CVP"), "validation_actors", "synthetic-handoff"), "continuation_actors")
            with self.assertRaises(MemoryError):
                budget.classify("synthetic-handoff/group-9/C", "reviewed_targeted_repairs", "synthetic-handoff")
            append_event(root / "ledger/requests.jsonl", {"logical_call_id": "unowned-new-call", "allocation": "validation_actors",
                                                         "reserved_input_output_tokens": 10})
            with self.assertRaises(MemoryError):
                budget.child_state()
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
