"""Close a source-gate-failed prefix using production replay and actual GPU lifetime."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path

from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.calibration_budget import verify_parent
from collusion_handoff.memory import require


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cache-audit", "phase", "global-root", "global-profile", "parent-receipt", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-review", type=Path, action="append", required=True)
    parser.add_argument("--source-gate-failed", action="store_true", required=True)
    parser.add_argument("--expected-attempts", type=int, required=True)
    parser.add_argument("--expected-tokens", type=int, required=True)
    parser.add_argument("--expected-allocated-seconds", type=int, required=True)
    args = parser.parse_args()
    require(os.getuid() != 0 and Path("/.dockerenv").exists() and os.environ.get("CUDA_VISIBLE_DEVICES") == "" and
            not list(Path("/dev").glob("nvidia[0-9]*")) and {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"},
            "Nonroot CPU Docker without GPU/network required")
    require(not args.output.exists() and len(args.source_review) >= 2, "New output and independently sealed source reviews required")
    audit, phase, parent = map(read_json, (args.cache_audit, args.phase / "phase-manifest.json", args.parent_receipt))
    profile = read_json(args.global_profile)
    verify_parent(args.global_root, parent, profile)
    require(digest(args.global_profile) == phase["global_profile_sha256"] and digest(args.parent_receipt) == phase["parent_closure_sha256"] and
            audit["status"] == "passed_cache_only" and audit["inference_commit"] == phase["code_commit"] and
            audit["phase"] == phase["phase"] and audit["new_model_calls"] == 0 and not audit["unknown_spend_requests"],
            "Matching successful production cache audit and unchanged parent required")
    state = read_json(args.phase / "phase-status.json")
    require(state["status"] == "finished_with_explicit_statuses" and state["failure"] is None and
            not (args.phase / "memory").exists(), "Failed/unfinished or memory-containing phase cannot close as a source-only stop")
    review_hashes = {str(path): digest(path) for path in args.source_review}
    require(review_hashes == audit["calibration_audit_profile"]["sealed_source_review_sha256"], "Sealed review files changed since replay")
    inspect_path = args.phase / "docker-inspect-final.json"
    inspect = read_json(inspect_path)[0]
    running, host, container = inspect["State"], inspect["HostConfig"], inspect["Config"]
    operations = phase["operations"]
    require(running["Running"] is False and running["ExitCode"] == 0 and running["OOMKilled"] is False and
            container["Labels"]["owner"] == "a.maslov" and container["Labels"]["adapter_commit"] == phase["code_commit"] and
            container["User"].split(":")[0] not in {"", "0", "root"}, "GPU phase owner/state is invalid")
    require(host["DeviceRequests"][0]["DeviceIDs"] == [operations["gpu_uuid"]] and len(host["DeviceRequests"]) == 1 and
            host["NanoCpus"] == operations["cpu_limit"] * 10**9 and host["Memory"] == operations["RAM_limit_GiB"] * 2**30 and
            host["ShmSize"] == operations["shm_GiB"] * 2**30 and host["PidsLimit"] == operations["pids_limit"] and
            host["IpcMode"] != "host" and host["Privileged"] is False and host["RestartPolicy"]["Name"] == "no",
            "GPU topology or resource limits differ from the frozen phase")
    elapsed = math.ceil((datetime.fromisoformat(running["FinishedAt"]) - datetime.fromisoformat(running["StartedAt"])).total_seconds())
    require(elapsed > 0, "Invalid actual GPU lifetime")
    allocated = parent["before"]["allocated_gpu_container_seconds"] + elapsed
    require((audit["all_v5_calls"], audit["tokens"], allocated) ==
            (args.expected_attempts, args.expected_tokens, args.expected_allocated_seconds), "Cumulative carry differs from the reviewed next-parent expectation")
    ledgers = {}
    for name in ("requests.jsonl", "responses.jsonl"):
        path = args.global_root / "ledger" / name
        raw = path.read_bytes()
        require(raw.endswith(b"\n") and digest(path) == audit["calibration_audit_profile"]["global_ledger_sha256"][name] and
                len(raw.splitlines()) == audit["all_v5_calls"], "Global journal changed or contains unresolved spend")
        ledgers[name] = {"bytes": len(raw), "rows": len(raw.splitlines()), "sha256": digest(path)}
    result = deepcopy(parent)
    result.update(created_at_utc=datetime.now(timezone.utc).isoformat(), latest_closed_child_run_id=phase["run_id"],
        latest_config_sha256=phase["config_sha256"], latest_inference_code_commit=phase["code_commit"],
        parent_closure_sha256=digest(args.parent_receipt), previous_closed_ledger_prefix=parent["ledger_prefix"], ledger_prefix=ledgers,
        scientific_readiness="Source-only qualification failed; dependent memory stage stopped. No inheritance or filter efficacy claim.",
        new_model_calls_for_closure=0, main_allowed=False)
    result["before"] = {"attempts": audit["all_v5_calls"], "input_output_tokens": audit["tokens"],
        "allocated_gpu_container_seconds": allocated, "allocations": audit["allocations"], "unknown_spend_requests": []}
    result["remaining_allocations"] = {name: cap - audit["allocations"].get(name, 0) for name, cap in result["allocations"].items()}
    result["audit_evidence_sha256"].update({str(args.cache_audit): digest(args.cache_audit), str(inspect_path): digest(inspect_path), **review_hashes})
    stratum = phase["runtime_hardware_override"]["hardware_stratum"] + "_closed"
    require(stratum not in result["strata"], "Explicit stratum merge required instead of overwriting prior accounting")
    result["strata"][stratum] = {"calls": audit["phase_calls_replayed"], "tokens": audit["tokens"] - parent["before"]["input_output_tokens"],
                                  "allocated_ceil_seconds": elapsed}
    result["source_gate"] = {"status": "not_qualifying_stop_dependent_stage", "review_sha256": review_hashes,
                             "human_gold": False, "memory_outputs_generated": False}
    atomic_json(args.output, result)
    print(json.dumps({"status": result["status"], "closed_parent_sha256": digest(args.output), "new_model_calls": 0}))


if __name__ == "__main__":
    main()
