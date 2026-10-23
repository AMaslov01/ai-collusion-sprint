"""Single-attempt actor transport with a journal of the actual HTTP body.

Job admission, budgets, cache reuse and resume belong to the runner. This
transport does not grant permission to call a model or implement retries.
"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from threading import Lock
import time
from typing import Callable, Mapping

import httpx


SAMPLER_FIELDS = (
    "temperature", "top_p", "top_k", "min_p",
    "presence_penalty", "repetition_penalty",
)
ACTOR_ROLES = frozenset({"A", "A_prime", "B"})


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class WireJournal:
    """Append and fsync request/response rows in an existing private IO directory."""

    def __init__(self, directory: Path):
        self.directory = Path(directory).absolute()
        if self.directory.resolve() != self.directory:
            raise ValueError("IO directory must not traverse symlinks")
        info = self.directory.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("IO directory must be owned by this UID and private")
        if not self.directory.is_dir():
            raise ValueError("IO directory is not a directory")

    def append(self, stream: str, row: Mapping) -> None:
        if stream not in {"requests", "responses"}:
            raise ValueError("Unknown journal stream")
        path = self.directory / f"{stream}.jsonl"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as output:
            info = os.fstat(output.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Journal must be a regular file")
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Journal must be owned by this UID and private")
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()
            os.fsync(output.fileno())


class ActorRequestPolicy:
    def __init__(self, *, generation: Mapping, served_alias: str, max_output: int, n: int):
        if not served_alias or "/" in served_alias or served_alias == "UNSET":
            raise ValueError("A confirmed safe serving alias is required")
        if type(max_output) is not int or max_output <= 0:
            raise ValueError("An explicit positive output cap is required")
        if type(n) is not int or n != 1:
            raise ValueError("This actor transport requires explicit n=1")
        for field in SAMPLER_FIELDS:
            if type(generation[field]) not in {int, float}:
                raise ValueError(f"Unresolved sampler field: {field}")
        if type(generation["top_k"]) is not int:
            raise ValueError("top_k must be an integer")
        kwargs = generation["chat_template_kwargs"]
        if kwargs.get("enable_thinking") is not False:
            raise ValueError("DEV actors require boolean enable_thinking=false")
        if type(generation["seed"]) is not int:
            raise ValueError("An explicitly confirmed actor seed is required")
        self.sampler = {field: generation[field] for field in SAMPLER_FIELDS}
        self.sampler["chat_template_kwargs"] = deepcopy(kwargs)
        self.sampler["seed"] = generation["seed"]
        self.sampler["n"] = n
        self.served_alias = served_alias
        self.max_output = max_output

    def apply(self, payload: Mapping) -> dict:
        if payload.get("model") != self.served_alias:
            raise ValueError("Unexpected model alias")
        if payload.get("stream"):
            raise ValueError("Streaming is outside the native DEV contract")
        if "max_completion_tokens" in payload or "extra_body" in payload:
            raise ValueError("Expected the serialized native chat-completions body")
        requested_cap = payload.get("max_tokens", self.max_output)
        if type(requested_cap) is not int or requested_cap <= 0:
            raise ValueError("Invalid native output cap")
        result = deepcopy(dict(payload))
        result.update(deepcopy(self.sampler))
        result["max_tokens"] = min(requested_cap, self.max_output)
        return result


class ActorGatewayTransport(httpx.BaseTransport):
    def __init__(
        self, *, inner: httpx.BaseTransport, policy: ActorRequestPolicy,
        journal: WireJournal, call_context: Callable[[], Mapping],
        before_call: Callable[[Mapping], None],
    ):
        self.inner = inner
        self.policy = policy
        self.journal = journal
        self.call_context = call_context
        self.before_call = before_call
        self.queue = Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        with self.queue:
            return self._handle_request(request)

    def _handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host not in {"127.0.0.1", "::1"}:
            raise ValueError("Only the local model endpoint is permitted")
        if request.url.scheme != "http" or request.url.path != "/v1/chat/completions":
            raise ValueError("Unexpected model endpoint")
        if request.method != "POST" or request.url.query:
            raise ValueError("Unexpected model operation")
        context = dict(self.call_context())
        if context.get("role") not in ACTOR_ROLES:
            raise ValueError("Actor sampler cannot be applied to memory builders")
        if not context.get("logical_call_id") or type(context.get("attempt")) is not int:
            raise ValueError("Explicit logical call identity and attempt are required")
        if context["attempt"] < 1:
            raise ValueError("Invalid attempt number")
        incoming = request.read().decode("utf-8")
        effective = self.policy.apply(json.loads(incoming))
        self.before_call(effective)
        outgoing = json.dumps(
            effective,
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        headers = request.headers.copy()
        headers.pop("content-length", None)
        wire_request = httpx.Request(
            request.method, request.url, headers=headers, content=outgoing,
            extensions=dict(request.extensions),
        )
        identity = {**context, "request_sha256": sha256(outgoing).hexdigest()}
        self.journal.append("requests", {
            **identity, "timestamp": _timestamp(),
            "incoming_body_utf8": incoming, "body_utf8": outgoing.decode("utf-8"),
        })
        started = time.monotonic()
        try:
            response = self.inner.handle_request(wire_request)
            response.read()
        except Exception as error:
            self.journal.append("responses", {
                **identity, "timestamp": _timestamp(),
                "duration_seconds": time.monotonic() - started,
                "transport_error_type": type(error).__name__, "unknown_spend": True,
            })
            raise
        self.journal.append("responses", {
            **identity, "timestamp": _timestamp(),
            "duration_seconds": time.monotonic() - started,
            "status_code": response.status_code,
            "body_utf8": response.content.decode("utf-8"),
        })
        return response

    def close(self) -> None:
        self.inner.close()


def install_native_actor_gateway(
    native_client, *, policy: ActorRequestPolicy, journal: WireJournal,
    call_context: Callable[[], Mapping], before_call: Callable[[Mapping], None],
    timeout_seconds: float, inner: httpx.BaseTransport | None = None,
) -> None:
    """Replace only the native SDK transport; preserve MemoryArena tool formatting."""
    if timeout_seconds <= 0:
        raise ValueError("An explicit positive call timeout is required")
    if native_client.model_name != policy.served_alias:
        raise ValueError("Native client must use the confirmed serving alias")
    old_client = native_client.client
    if old_client.base_url.host not in {"127.0.0.1", "::1"}:
        raise ValueError("Native client must be configured for the local endpoint")
    transport = ActorGatewayTransport(
        inner=inner if inner is not None else httpx.HTTPTransport(retries=0),
        policy=policy, journal=journal, call_context=call_context, before_call=before_call,
    )
    http_client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False)
    native_client.client = old_client.with_options(
        http_client=http_client, api_key="local-inference", max_retries=0,
        timeout=timeout_seconds,
    )
    old_client.close()
