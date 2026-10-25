"""Retain the production distinction between per-task and phase-stopping failures."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("main_cache_audit_script",
    Path(__file__).resolve().parents[1] / "scripts/audit-main-cache.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MainCacheReplayTests(unittest.TestCase):
    def test_budget_exhaustion_stops_phase_even_when_status_is_not_phase_stopped(self):
        record = {"status": "budget_exhausted", "failure": {
            "type": "AdmissionFailure", "kind": "budget_exhausted", "message": "Ceiling reached"}}
        self.assertTrue(module.admission_phase_stop(record))
        record["status"] = "timeout"
        record["failure"]["kind"] = "timeout"
        self.assertFalse(module.admission_phase_stop(record))
        record["failure"]["kind"] = "unknown_new_semantics"
        with self.assertRaises(RuntimeError):
            module.admission_phase_stop(record)


if __name__ == "__main__":
    unittest.main()
