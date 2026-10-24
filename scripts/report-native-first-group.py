"""Read-only native report from completed artifacts; no endpoint or model client."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re

from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.checkpoint import load_checkpoint
from collusion_handoff.smoke import journal_rows


def main():
    if os.getuid() == 0:
        raise RuntimeError("Requires non-root Docker")
    os.umask(0o077)
    root = Path("/native")
    config = read_json("/code/configs/dev-package.draft.json")
    manifest = read_json(root / "manifest.json")
    scientific = manifest["scientific_fingerprint"]
    assert fingerprint(manifest["scientific"]) == scientific
    audit = read_json(root / "audit.json")
    assert audit["status"] == "passed" and audit["model_calls_during_audit"] == 0
    requests = journal_rows(root / "io/requests.jsonl")
    responses = journal_rows(root / "io/responses.jsonl")
    assert len(requests) == len(responses) == audit["cached_model_responses_consumed"]
    expected_generation = {key: config["generation"][key] for key in (
        "temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty", "seed", "chat_template_kwargs")}
    group = read_json(root / "checkpoints/group-1/traveler-0/admin.json")["group"]
    jobs = {}
    for request, response in zip(requests, responses):
        assert request["logical_call_id"] == response["logical_call_id"]
        assert request["request_sha256"] == response["request_sha256"]
        assert sha256(request["body_utf8"].encode()).hexdigest() == request["request_sha256"]
        assert request["attempt"] == response["attempt"] == 1 and response["status_code"] == 200
        match = re.fullmatch(r"(native/group-1/traveler-(\d+))/A/turn-(\d+)", request["logical_call_id"])
        assert match
        job_id, number, turn = match.group(1), int(match.group(2)), int(match.group(3))
        payload = json.loads(request["body_utf8"])
        assert {key: payload[key] for key in expected_generation} == expected_generation
        assert payload["n"] == 1 and payload["max_tokens"] == 4096 and payload["model"] == config["model"]["served_alias"]
        text = json.dumps(payload["messages"], ensure_ascii=False)
        for question in group["questions"]:
            if question["round_idx"] > number:
                assert question["query"] not in text, "Unissued complete traveler query appeared in actor messages"
        raw = json.loads(response["body_utf8"])
        assert len(raw["choices"]) == 1
        usage = raw["usage"]
        assert usage["prompt_tokens"] <= 28672 and usage["completion_tokens"] <= 4096
        assert usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0) == 0
        records = jobs.setdefault(job_id, [])
        assert turn == len(records) + 1
        records.append({"input": usage["prompt_tokens"], "output": usage["completion_tokens"], "duration_seconds": response["duration_seconds"]})
    summaries = []
    for number in (1, 2, 3, 4):
        job_id = f"native/group-1/traveler-{number}"
        result = read_json(root / f"results/group-1-traveler-{number}.json")
        checkpoint, _ = load_checkpoint(root / f"checkpoints/group-1/traveler-{number}", scientific)
        assert checkpoint == result["checkpoint_manifest"]
        calls = jobs[job_id]
        tokens = sum(row["input"] + row["output"] for row in calls)
        assert len(calls) == result["model_calls"] <= 30
        assert tokens == result["total_input_output_tokens"] <= 200000
        assert result["duration_seconds"] <= 1800
        summaries.append({key: result[key] for key in (
            "job_id", "model_calls", "total_input_output_tokens", "duration_seconds", "parse_valid", "protocol_valid",
            "completion", "native_agent_success_flag", "native_approximate_secondary_reward")})
    coverage = read_json(root / "coverage.json")
    assert len(coverage["jobs"]) == 48
    assert sum(row["status"] == "verified" for row in coverage["jobs"]) == 4
    assert sum(row["status"] == "not_run" for row in coverage["jobs"]) == 44
    assert ", 0 MiB," in (root / "gpu-after.csv").read_text()
    gpu = read_json(root / "docker-inspect-final.json")[0]
    assert gpu["State"]["ExitCode"] == 0 and not gpu["State"]["OOMKilled"]
    assert gpu["HostConfig"]["DeviceRequests"][0]["DeviceIDs"] == [config["operations"]["gpu_uuid"]]
    samples = [int(line.split(",")[1].split()[0]) for line in (root / "vram-samples.csv").read_text().splitlines() if line.startswith("GPU-")]
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix in {".json", ".jsonl", ".log", ".csv", ".prom"} and not path.is_relative_to(root / "tmp")]
    identities = [{"path": str(path.relative_to(root)), "sha256": sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size} for path in sorted(files)]
    report = {"status": "cache_only_report_verified", "model_calls_during_report": 0,
              "native_group_id": 1, "verified_new_travelers": 4, "assigned_native_jobs": 48,
              "remaining_not_run": 44, "actual_model_attempts": len(requests), "retries": 0,
              "results": summaries, "snapshot_2_request_3_sha256": audit["snapshot_2_request_3_sha256"],
              "scientific_fingerprint": scientific, "sampled_peak_vram_mib": max(samples), "gpu_memory_after_mib": 0,
              "next_gate": "memory_contract_and_source_first_human_review", "main_started": False,
              "artifact_files": identities}
    atomic_json(Path("/work/native-first-group-report.json"), report)
    print(json.dumps({key: value for key, value in report.items() if key != "artifact_files"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
