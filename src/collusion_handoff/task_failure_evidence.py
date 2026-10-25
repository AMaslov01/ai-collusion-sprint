"""Preserve missing task outcomes without interpreting missing records as safety."""

from pathlib import Path
import traceback

from .calibration_budget import journal
from .memory import require


def failure_accounting(root):
    try:
        require(root is not None, "No authoritative spending journal bound to this task")
        path = Path(root) / "ledger/requests.jsonl"
        require(path.is_file(), "Missing global requests journal during failure recovery")
        rows = journal(path)
        require(all(isinstance(row, dict) and isinstance(row.get("logical_call_id"), str) for row in rows),
                "Unreadable logical identities in global requests journal")
        return rows, None
    except Exception:
        return None, traceback.format_exc()


def unknown_task_result(task_id, branch, requests, reason, accounting_error=None):
    calls = None if requests is None else sum(row["logical_call_id"].startswith(branch + "/" + task_id + "/") for row in requests)
    return {"task_id": task_id, "status": "measurement_corruption" if requests is None else
            "interrupted_before_durable_task_result" if calls else "not_run_after_phase_failure",
            "reason": reason, "model_calls": calls, "evaluation": {"H": None, "U": None, "Q": None},
            "accounting_error": accounting_error, "event_recovery_required_before_outcome_claim": requests is None or bool(calls)}
