"""Cache-only audit of the completed smoke. This script has no model client."""

import csv
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re


def read(path):
    return json.loads(path.read_text())


def main():
    if os.getuid() == 0:
        raise RuntimeError("Requires non-root Docker")
    os.umask(0o077)
    root = Path("/smoke")
    config = read(Path("/code/configs/dev-package.draft.json"))
    requests = [json.loads(line) for line in (root / "io/requests.jsonl").read_text().splitlines()]
    responses = [json.loads(line) for line in (root / "io/responses.jsonl").read_text().splitlines()]
    assert len(requests) == len(responses) == 3
    counts = read(root / "input-token-counts.json")
    calls = []
    payloads = []
    raw_responses = []
    fields = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty", "chat_template_kwargs")
    for index, (request, response) in enumerate(zip(requests, responses)):
        payload = json.loads(request["body_utf8"])
        assert sha256(request["body_utf8"].encode()).hexdigest() == request["request_sha256"]
        assert request["logical_call_id"] == response["logical_call_id"]
        assert request["request_sha256"] == response["request_sha256"]
        assert request["attempt"] == response["attempt"] == 1
        assert response["status_code"] == 200
        assert {key: payload[key] for key in fields} == {key: config["generation"][key] for key in fields}
        assert payload["n"] == 1 and payload["max_tokens"] == 4096 and payload["seed"] == 42
        assert payload["model"] == "qwen-smoke"
        assert json.loads(request["incoming_body_utf8"])["temperature"] == 0.0
        raw = json.loads(response["body_utf8"])
        assert raw["usage"]["prompt_tokens"] == counts[index] <= 28672
        assert raw["usage"]["completion_tokens"] <= 4096
        assert raw["model"] == "qwen-smoke" and len(raw["choices"]) == 1
        calls.append({
            "logical_call_id": request["logical_call_id"], "request_sha256": request["request_sha256"],
            "duration_seconds": response["duration_seconds"], "usage": raw["usage"],
            "finish_reason": raw["choices"][0]["finish_reason"],
        })
        payloads.append(payload)
        raw_responses.append(raw)
    assert [item["logical_call_id"] for item in calls] == ["smoke/simple", "smoke/tool-call", "smoke/final-plan"]
    tool_call = raw_responses[1]["choices"][0]["message"]["tool_calls"]
    assert len(tool_call) == 1
    tool_call = tool_call[0]
    assert tool_call["function"]["name"] == "AccommodationSearch"
    assert json.loads(tool_call["function"]["arguments"]) == {"city": "Rockford"}
    receipt = read(root / "tool-receipt.json")
    assert receipt["tool_call_id"] == tool_call["id"]
    assert receipt["result_sha256"] == sha256(receipt["exact_displayed_result"].encode()).hexdigest()
    dependency_identity = Path("/dependency-check/travel-tools-identity.json")
    assert sha256(dependency_identity.read_bytes()).hexdigest() == config["runtime"]["travel_tool_dependencies"]["identity"]["sha256"]
    assert receipt["result_sha256"] == read(dependency_identity)["result_sha256"]
    tool_messages = [message for message in payloads[2]["messages"] if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == tool_call["id"]
    assert tool_messages[0]["content"] == receipt["exact_displayed_result"]
    result = read(root / "smoke-result.json")
    assert result["status"] == read(root / "status.json")["status"] == "passed"
    assert result["native_dev_started"] is False and result["main_started"] is False
    final = result["final_plan"]
    prices = {Decimal(value) for value in re.findall(r"\d+(?:\.\d+)?", final.replace(",", ""))}
    with Path("/database/accommodations/clean_accommodations_2022.csv").open() as source:
        matches = [row for row in csv.DictReader(source) if row["city"] == "Rockford" and row["NAME"].lower() in final.lower() and Decimal(row["price"]) in prices]
    assert matches, "Final plan does not identify a canonical accommodation and its price"
    inspect = read(root / "docker-inspect-final.json")[0]
    host = inspect["HostConfig"]
    assert inspect["State"]["ExitCode"] == 0 and not inspect["State"]["OOMKilled"]
    assert inspect["Config"]["User"] == f"{os.getuid()}:{os.getgid()}"
    assert host["NanoCpus"] == 8_000_000_000 and host["Memory"] == 64 * 1024**3
    assert host["PidsLimit"] == 1024 and host["ShmSize"] == 8 * 1024**3 and host["IpcMode"] == "private"
    assert host["RestartPolicy"]["Name"] == "no"
    assert host["DeviceRequests"][0]["DeviceIDs"] == [config["operations"]["gpu_uuid"]]
    for mount in inspect["Mounts"]:
        if mount["Destination"] in {"/code", "/model", "/database", "/vendor", "/opt/ai-collusion/sources/MemoryArena"}:
            assert mount["RW"] is False
    assert ", 0 MiB," in (root / "gpu-after.csv").read_text()
    samples = [int(line.split(",")[1].split()[0]) for line in (root / "vram-samples.csv").read_text().splitlines() if line.startswith("GPU-")]
    resolved = read(root / "resolved-engine.json")
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix in {".json", ".jsonl", ".log", ".csv", ".prom"} and not path.is_relative_to(root / "tmp")]
    identities = [{"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest()} for path in sorted(files)]
    packages = {}
    for name in ("datasets", "pyarrow", "pandas", "vllm", "openai"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    audit = {
        "status": "passed", "model_calls_during_audit": 0, "completed_smoke_calls": 3,
        "calls": calls, "resolved_model_dtype": resolved["model_dtype"],
        "resolved_kv_dtype": resolved["kv_tensor_dtype"],
        "sampled_peak_vram_mib": max(samples), "gpu_memory_after_mib": 0,
        "canonical_final_accommodation_matches": [{"name": row["NAME"], "price": row["price"]} for row in matches],
        "package_inventory_for_next_stage": packages, "artifact_files": identities,
        "native_dev_started": False, "main_started": False,
    }
    Path("/work/audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps({key: value for key, value in audit.items() if key != "artifact_files"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
