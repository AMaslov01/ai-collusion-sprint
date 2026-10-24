"""Bounded technical health check, separate from either DEV collection block."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback

import httpx

from .model_gateway import ActorRequestPolicy, WireJournal, install_native_actor_gateway


ALIAS = "qwen-smoke"
URL = "http://127.0.0.1:18083"
WORK = Path("/work")
CONFIG = Path("/code/configs/dev-package.draft.json")
SMOKE_CONTRACT = {
    "tensor_parallel_size": 1, "max_model_len": 32768,
    "client_concurrency": 1, "max_num_seqs": 1, "n": 1,
    "max_input_tokens": 28672, "max_output_tokens_per_call": 4096,
    "kv_cache_dtype": "auto", "gpu_memory_utilization": 0.90,
    "tool_call_parser": "qwen3_coder", "reasoning_parser": "qwen3",
    "seed": 42, "request_timeout_seconds": 900,
    "shared_memory_bytes": 8589934592, "pids_limit": 1024,
    "transport_attempts_per_request": 1,
}


def save(name, value):
    destination = WORK / name
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, destination)


def admission(deadline, stop=Path("/control/STOP")):
    if stop.exists():
        raise RuntimeError("STOP requested; no new work admitted")
    if time.time() >= deadline:
        raise RuntimeError("Persistent package deadline reached")


def journal_rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith("\n")]


def server_command(config, alias=ALIAS):
    smoke = config["smoke"]
    if any(smoke[key] != value for key, value in SMOKE_CONTRACT.items()):
        raise ValueError("Smoke configuration differs from the approved contract")
    return [
        "vllm", "serve", "/model", "--served-model-name", alias,
        "--tensor-parallel-size", str(smoke["tensor_parallel_size"]),
        "--max-model-len", str(smoke["max_model_len"]),
        "--max-num-seqs", str(smoke["max_num_seqs"]),
        "--kv-cache-dtype", smoke["kv_cache_dtype"],
        "--gpu-memory-utilization", str(smoke["gpu_memory_utilization"]),
        "--enable-auto-tool-choice", "--tool-call-parser", smoke["tool_call_parser"],
        "--reasoning-parser", smoke["reasoning_parser"],
        "--seed", str(smoke["seed"]), "--host", "127.0.0.1", "--port", "18083",
    ]


def verify_inputs(config, alias=ALIAS):
    if os.getuid() == 0:
        raise RuntimeError("Smoke requires non-root Docker")
    assert version("vllm") == config["runtime"]["vllm_version"]
    identities = {}
    for kind, reference, root in (
        ("model", config["model"]["weight_hashes"], Path("/model")),
        ("catalog", config["data"]["tool_input_manifest"], Path("/database")),
    ):
        path = Path("/identities") / Path(reference["path"]).name
        content = path.read_bytes()
        if sha256(content).hexdigest() != reference["sha256"]:
            raise RuntimeError(f"{kind} identity file mismatch")
        identity = json.loads(content)
        for item in identity["files"]:
            source = root / item["path"]
            if not source.resolve().is_relative_to(root) or not source.is_file():
                raise RuntimeError(f"Invalid input path: {source}")
            digest = sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != item["sha256"] or source.stat().st_size != item["bytes"]:
                raise RuntimeError(f"Input checksum mismatch: {source}")
        identities[kind] = {"identity_sha256": reference["sha256"], "files_verified": len(identity["files"])}
    model_config = json.loads(Path("/model/config.json").read_text())
    templates = {
        str(path.relative_to("/model")): sha256(path.read_bytes()).hexdigest()
        for path in Path("/model").glob("*chat_template*") if path.is_file()
    }
    save("verified-inputs.json", {
        "identities": identities, "chat_templates": templates,
        "tokenizer_config_sha256": sha256(Path("/model/tokenizer_config.json").read_bytes()).hexdigest(),
        "model_architectures": model_config.get("architectures"),
        "adapter_commit": os.environ["ADAPTER_COMMIT"],
        "config_sha256": sha256(CONFIG.read_bytes()).hexdigest(),
        "server_argv": server_command(config, alias), "model_calls": 0,
    })


class InputBudget:
    def __init__(self, http, deadline, *, stop=Path("/control/STOP")):
        self.http = http
        self.deadline = deadline
        self.stop = stop
        self.counts = []

    def __call__(self, payload):
        admission(self.deadline, self.stop)
        if time.time() + 900 >= self.deadline:
            raise RuntimeError("Less than one full request timeout remains in the package budget")
        if len(self.counts) >= 3:
            raise RuntimeError("Smoke admits exactly three model attempts at most")
        tokenize = {
            key: payload[key] for key in ("model", "messages", "tools", "chat_template_kwargs")
            if key in payload
        }
        response = self.http.post(URL + "/tokenize", json=tokenize)
        response.raise_for_status()
        result = response.json()
        if result["max_model_len"] != 32768 or type(result["count"]) is not int:
            raise RuntimeError("Unexpected tokenizer context or token count")
        if result["count"] > 28672 or result["count"] + payload["max_tokens"] > 32768:
            raise RuntimeError("Fully formatted input exceeds the approved context budget")
        admission(self.deadline, self.stop)
        self.counts.append(result["count"])
        save("input-token-counts.json", self.counts)


def validate_response(response, expected_input):
    raw = response.raw_response
    if len(raw.choices) != 1:
        raise RuntimeError("Expected exactly one completion")
    choice = raw.choices[0]
    if choice.finish_reason not in {"stop", "tool_calls"}:
        raise RuntimeError(f"Incomplete smoke response: {choice.finish_reason}")
    if raw.usage is None or raw.usage.prompt_tokens != expected_input:
        raise RuntimeError("Generation input differs from the pre-send tokenizer count")
    if raw.usage.completion_tokens > 4096:
        raise RuntimeError("Output cap violated")
    if response.content and re.search(r"<(?:tool_call|function[=>]|think>)", response.content):
        raise RuntimeError("Tool/reasoning markup leaked into ordinary content")
    return raw.model_dump(mode="json")


def client_check(config, deadline):
    from env.env_systems.travel_planner_env.clients.openai_client import OpenAIClient
    from env.env_systems.travel_planner_env.tool_executor import ToolExecutor
    from env.env_systems.travel_planner_env.tool_schemas import TOOLS

    tools = [tool for tool in TOOLS if tool["function"]["name"] == "AccommodationSearch"]
    assert len(tools) == 1
    executor = ToolExecutor(db_path="/database")
    context = {"logical_call_id": "smoke/simple", "role": "A", "attempt": 1}
    generation = {**config["generation"], "seed": config["smoke"]["seed"]}
    WORK.joinpath("io").mkdir(mode=0o700)
    native = OpenAIClient(model_name=ALIAS, api_key="local-inference", base_url=URL + "/v1")
    records = []
    with httpx.Client(timeout=900, trust_env=False, follow_redirects=False) as http:
        budget = InputBudget(http, deadline)
        install_native_actor_gateway(
            native, policy=ActorRequestPolicy(generation=generation, served_alias=ALIAS, max_output=4096, n=1),
            journal=WireJournal(WORK / "io"), call_context=lambda: context,
            before_call=budget, timeout_seconds=900,
        )
        try:
            simple = native.chat_with_tools([{"role": "user", "content": "Reply with READY."}], [])
            records.append(validate_response(simple, budget.counts[-1]))
            if simple.tool_calls or not simple.content or not simple.content.strip():
                raise RuntimeError("Simple-answer smoke did not return text")
            messages = [
                {"role": "system", "content": "You are a travel planning assistant. Search before recommending accommodation."},
                {"role": "user", "content": "Call AccommodationSearch with city Rockford. Wait for the search result before drafting the plan."},
            ]
            context["logical_call_id"] = "smoke/tool-call"
            search = native.chat_with_tools(messages, tools)
            records.append(validate_response(search, budget.counts[-1]))
            if not search.tool_calls or len(search.tool_calls) != 1:
                raise RuntimeError("Expected one genuine API tool call")
            call = search.tool_calls[0]
            if not call.id or call.name != "AccommodationSearch" or call.arguments != {"city": "Rockford"}:
                raise RuntimeError("Unexpected tool identity or arguments")
            admission(deadline)
            started = time.monotonic()
            result = executor.execute(call.name, call.arguments)
            if not result or result.startswith("Error") or "There is no attraction" in result:
                raise RuntimeError("Canonical search tool failed")
            save("tool-receipt.json", {
                "tool_call_id": call.id, "name": call.name, "arguments": call.arguments,
                "exact_displayed_result": result, "result_sha256": sha256(result.encode()).hexdigest(),
                "duration_seconds": time.monotonic() - started,
                "catalog_identity_sha256": config["data"]["tool_input_manifest"]["sha256"],
            })
            messages += [native.format_assistant_tool_calls(search.tool_calls), native.format_tool_result(call.id, result)]
            messages.append({"role": "user", "content": "Using that result, give a brief plan for a one-night stay in Rockford. Recommend one listed accommodation and include its name and listed price. Do not call another tool."})
            context["logical_call_id"] = "smoke/final-plan"
            final = native.chat_with_tools(messages, tools)
            records.append(validate_response(final, budget.counts[-1]))
            if final.tool_calls or not final.content or not final.content.strip():
                raise RuntimeError("Tool continuation did not produce a final plan")
            save("smoke-result.json", {
                "status": "passed", "scope": "technical_smoke_only", "model_calls": len(records),
                "responses": records, "input_token_counts": budget.counts,
                "final_plan": final.content, "native_dev_started": False, "main_started": False,
            })
        finally:
            native.client.close()


def resolved_engine():
    from vllm.utils.torch_utils import get_kv_cache_torch_dtype

    text = (WORK / "server.log").read_text(errors="replace")
    lines = [line for line in text.splitlines() if "Initializing a V1 LLM engine" in line]
    if len(lines) != 1:
        raise RuntimeError("Could not identify a unique resolved engine config")
    line = lines[0]
    dtype = re.search(r"\bdtype=torch\.(\w+),", line)
    kv_dtype = re.search(r"\bkv_cache_dtype=(\w+),", line)
    if not dtype or not kv_dtype or kv_dtype.group(1) != "auto":
        raise RuntimeError("Unexpected resolved model or KV dtype")
    for expected in ("max_seq_len=32768,", "tensor_parallel_size=1,", "seed=42,", "quantization=fp8,"):
        if expected not in line:
            raise RuntimeError(f"Missing resolved engine setting: {expected}")
    save("resolved-engine.json", {
        "startup_config_line": line, "model_dtype": "torch." + dtype.group(1),
        "kv_cache_dtype_setting": kv_dtype.group(1),
        "kv_tensor_dtype": str(get_kv_cache_torch_dtype(kv_dtype.group(1), dtype.group(1))),
        "dtype_basis": "Actual startup config passed through the pinned vLLM dtype resolver",
    })


def supervise(config, deadline, *, client_module="collusion_handoff.smoke", alias=ALIAS, scope="technical_smoke_only", server_extra_args=()):
    children = []
    interrupted = False

    def stop_signal(signum, frame):
        nonlocal interrupted
        interrupted = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop_signal)

    def check():
        if interrupted:
            raise RuntimeError("Supervisor received a stop signal")
        admission(deadline)

    try:
        check()
        verify_inputs(config, alias)
        with (WORK / "server.log").open("x") as server_log, (WORK / "client.log").open("x") as client_log:
            actual_command = server_command(config, alias) + list(server_extra_args)
            save("actual-server-command.json", {"argv": actual_command})
            server = subprocess.Popen(actual_command, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
            children.append(server)
            with httpx.Client(timeout=1, trust_env=False) as health:
                while True:
                    check()
                    if server.poll() is not None:
                        raise RuntimeError(f"vLLM startup failed with exit {server.returncode}")
                    try:
                        ready = health.get(URL + "/health").status_code == 200
                    except httpx.TransportError:
                        ready = False
                    if ready:
                        break
                    time.sleep(1)
            resolved_engine()
            client = subprocess.Popen([sys.executable, "-m", client_module, "--client"], stdout=client_log, stderr=subprocess.STDOUT, start_new_session=True)
            children.append(client)
            while client.poll() is None:
                check()
                if server.poll() is not None:
                    raise RuntimeError("vLLM exited during the smoke client")
                current_job = WORK / "current-job.json"
                if current_job.exists() and time.time() >= json.loads(current_job.read_text())["deadline_unix"]:
                    raise RuntimeError("Native traveler wall-time budget exhausted")
                request_path = WORK / "io/requests.jsonl"
                response_path = WORK / "io/responses.jsonl"
                if request_path.exists():
                    requests = journal_rows(request_path)
                    responses = journal_rows(response_path)
                    if len(requests) > len(responses):
                        started = datetime.fromisoformat(requests[-1]["timestamp"]).timestamp()
                        if time.time() - started >= 900:
                            raise RuntimeError("One model attempt reached its 900-second wall timeout; spend unknown")
                time.sleep(0.2)
            if client.returncode != 0:
                raise RuntimeError(f"Smoke client failed with exit {client.returncode}; inspect client.log")
            status_file = "status.json" if scope == "technical_smoke_only" else "server-session-status.json"
            save(status_file, {"status": "passed", "scope": scope, "finished_at": datetime.now(timezone.utc).isoformat()})
    except BaseException:
        save("status.json", {"status": "stopped_needs_review", "traceback": traceback.format_exc(), "finished_at": datetime.now(timezone.utc).isoformat()})
        raise
    finally:
        for child in reversed(children):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        until = time.monotonic() + 20
        while any(child.poll() is None for child in children) and time.monotonic() < until:
            time.sleep(0.2)
        for child in reversed(children):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    deadline = int(os.environ["PACKAGE_DEADLINE_UNIX"])
    admission(deadline)
    server_command(config)
    if args.client:
        client_check(config, deadline)
    elif args.validate_only:
        verify_inputs(config)
    else:
        supervise(config, deadline)


if __name__ == "__main__":
    main()
