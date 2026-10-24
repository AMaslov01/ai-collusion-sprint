"""Persistent, single-attempt accounting for the newly authorized v3 block."""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import time
import traceback

import httpx

from .artifacts import append_event, atomic_json, canonical, fingerprint
from .memory import MemoryError, require, strict_json
from .smoke import admission, journal_rows, URL


def utc():
    return datetime.now(timezone.utc).isoformat()


class ContextOverflow(MemoryError):
    pass


class BudgetExceeded(MemoryError):
    pass


class AccountingFailure(MemoryError):
    pass


class CycleBudget:
    def __init__(self, root, contract, http, *, phase_work, clock=time.time):
        self.root, self.contract, self.http = Path(root), contract, http
        self.phase_work, self.clock = Path(phase_work), clock
        self.ledger = self.root / "ledger"
        self.ledger.mkdir(exist_ok=True, mode=0o700)
        self.pending = None

    def deadline(self):
        cutoff = datetime.fromisoformat(self.contract["limits"]["absolute_cutoff_utc"]).timestamp()
        if self.contract["limits"].get("gpu_service_accounting") == "sum_allocated_container_lifetimes":
            service = self.root / "gpu-service-deadline.unix"
            require(service.is_file(), "Persistent service-time admission deadline missing")
            return min(cutoff, float(service.read_text()))
        start = self.root / "gpu-first-start.unix"
        return min(cutoff, float(start.read_text()) + self.contract["limits"]["gpu_service_wall_seconds"]) if start.exists() else cutoff

    def state(self):
        def rows(path):
            data = path.read_bytes() if path.exists() else b""
            require(not data or data.endswith(b"\n"), "Truncated global ledger; unknown spend blocks inference")
            return [strict_json(line) for line in data.decode().splitlines()]
        requests = rows(self.ledger / "requests.jsonl")
        responses = rows(self.ledger / "responses.jsonl")
        require(len({row["logical_call_id"] for row in requests}) == len(requests), "Duplicate global request identity")
        by_id = {row["logical_call_id"]: row for row in responses}
        require(len(by_id) == len(responses), "Duplicate global response receipt")
        tokens, unknown = 0, []
        allocations = {}
        for row in requests:
            allocations[row["allocation"]] = allocations.get(row["allocation"], 0) + 1
            response = by_id.get(row["logical_call_id"])
            if response is not None and response.get("accounted_actual_tokens") is not None:
                tokens += response["accounted_actual_tokens"]
            else:
                tokens += row["reserved_input_output_tokens"]
                unknown.append(row["logical_call_id"])
        return {"attempts": len(requests), "accounted_input_output_tokens": tokens,
                "allocations": allocations, "unknown_spend_requests": unknown,
                "logical_ids": [row["logical_call_id"] for row in requests]}

    def check(self, logical_id=None, allocation=None, reserve=0):
        admission(self.deadline())
        require(self.clock() < self.deadline(), "New v3 block deadline reached")
        state = self.state()
        if state["unknown_spend_requests"]:
            raise AccountingFailure("Unresolved or unknown spend blocks new requests; reserved budget is preserved")
        if logical_id is not None:
            require(logical_id not in state["logical_ids"], "Logical request already spent; no hidden retry")
            if state["attempts"] >= self.contract["limits"]["max_new_model_attempts"]:
                raise BudgetExceeded("Global attempt ceiling reached")
            require(allocation in self.contract["allocations"], "Unapproved allocation")
            if state["allocations"].get(allocation, 0) >= self.contract["allocations"][allocation]:
                raise BudgetExceeded("Stage allocation exhausted")
        if state["accounted_input_output_tokens"] + reserve > self.contract["limits"]["max_input_output_tokens"]:
            raise BudgetExceeded("Global token ceiling reached")
        return state

    def preview(self, payload, *, deadline=None):
        self.check()
        end = min(self.deadline(), deadline or self.deadline())
        require(end > self.clock(), "Request deadline already expired")
        token_request = {key: payload[key] for key in ("model", "messages", "tools", "chat_template_kwargs") if key in payload}
        response = self.http.post(URL + "/tokenize", json=token_request, timeout=min(900, end - self.clock()))
        response.raise_for_status()
        result = response.json()
        require(type(result["count"]) is int and result["max_model_len"] == 32768, "Unexpected tokenizer context")
        if result["count"] + payload["max_tokens"] > 32768:
            raise ContextOverflow("Fully rendered input overflow; no truncation")
        self.pending = {"payload_sha256": fingerprint(payload), "input_tokens": result["count"]}
        return result["count"]

    def dispatch(self, payload, logical_id, allocation, *, job=None, deadline=None, prepared=False):
        end = min(self.deadline(), deadline or self.deadline())
        if prepared:
            require(self.pending and self.pending["payload_sha256"] == fingerprint(payload), "Preflight request differs from actual wire")
            count = self.pending["input_tokens"]
        else:
            count = self.preview(payload, deadline=end)
        self.pending = None
        reserve = count + payload["max_tokens"]
        self.check(logical_id, allocation, reserve)
        timeout = min(900, end - self.clock())
        require(timeout > 0, "No request time remains")
        atomic_json(self.phase_work / "current-job.json", {"logical_call_id": logical_id, "deadline_unix": min(end, self.clock() + timeout)})
        body = canonical(payload)
        request = {"logical_call_id": logical_id, "allocation": allocation, "attempt": 1, "timestamp": utc(),
                   "body_utf8": body.decode(), "request_sha256": sha256(body).hexdigest(),
                   "rendered_input_tokens": count, "reserved_input_output_tokens": reserve, "timeout_seconds": timeout}
        append_event(self.ledger / "requests.jsonl", request)
        atomic_json(self.root / "global-budget.json", self.state())
        if job is not None:
            append_event(Path(job) / "io/requests.jsonl", request)
        started = self.clock()
        try:
            response = self.http.post(URL + "/v1/chat/completions", content=body, headers={"Content-Type": "application/json"}, timeout=timeout)
            receipt = {"logical_call_id": logical_id, "request_sha256": request["request_sha256"], "timestamp": utc(),
                       "duration_seconds": self.clock() - started, "status_code": response.status_code,
                       "body_utf8": response.text, "accounted_actual_tokens": None}
            if response.status_code == 200:
                try:
                    usage = strict_json(response.text)["usage"]
                    if usage["prompt_tokens"] == count and type(usage["completion_tokens"]) is int and 0 <= usage["completion_tokens"] <= payload["max_tokens"]:
                        receipt["accounted_actual_tokens"] = count + usage["completion_tokens"]
                except (ValueError, KeyError, TypeError):
                    pass
        except BaseException:
            receipt = {"logical_call_id": logical_id, "request_sha256": request["request_sha256"], "timestamp": utc(),
                       "transport_error": traceback.format_exc(), "accounted_actual_tokens": None, "unknown_spend": True}
            append_event(self.ledger / "responses.jsonl", receipt)
            if job is not None:
                append_event(Path(job) / "io/responses.jsonl", receipt)
            atomic_json(self.root / "global-budget.json", self.state())
            raise
        append_event(self.ledger / "responses.jsonl", receipt)
        if job is not None:
            append_event(Path(job) / "io/responses.jsonl", receipt)
        atomic_json(self.root / "global-budget.json", self.state())
        response.raise_for_status()
        if receipt["accounted_actual_tokens"] is None:
            raise AccountingFailure("Usage mismatch; spend reserved conservatively; stop phase")
        atomic_json(self.phase_work / "current-job.json", {"logical_call_id": logical_id, "deadline_unix": end})
        return response


class NativeCycleTransport(httpx.BaseTransport):
    def __init__(self, budget, native_budget, allocation):
        self.budget, self.native_budget, self.allocation = budget, native_budget, allocation

    def handle_request(self, request):
        payload = strict_json(request.content.decode())
        return self.budget.dispatch(payload, self.native_budget.last_context["logical_call_id"], self.allocation,
                                    deadline=self.native_budget.job_deadline, prepared=True)
