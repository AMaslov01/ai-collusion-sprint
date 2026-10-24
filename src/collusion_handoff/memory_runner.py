"""One approved group1/cp2 prototype, bounded by a persistent four-attempt ledger."""

import argparse
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import time
import traceback

import httpx
from transformers import AutoTokenizer

from . import smoke
from .artifacts import append_event, atomic_json, canonical, fingerprint, read_json
from .memory import MODEL_EVENT_FIELDS, build_packages, model_source_view, require, stage_messages, strict_json
from .memory_source import build_source


WORK = Path("/work")
CODE = Path("/code")
NATIVE = Path("/native")
UPSTREAM = Path("/opt/ai-collusion/sources/MemoryArena")
CONTRACT = CODE / "configs/memory-group1.json"
ALIAS = "qwen-native-dev"
SOURCE_SCIENCE = "895f88fc9e691d4725276758918a3609d3db421f106f25ef25b2897df8ec1719"


def utc():
    return datetime.now(timezone.utc).isoformat()


def task_deadline(contract):
    approved = datetime.fromisoformat(contract["limits"]["task_deadline_utc"]).timestamp()
    package = int(os.environ["PACKAGE_DEADLINE_UNIX"])
    require(package == 1792840396, "Persistent package deadline differs from the original 12-hour budget")
    require(approved == 1792840380, "Group1-only approved cutoff changed")
    require(contract["scope"] == "native_group1_checkpoint2_memory_prototype_only", "Unapproved scope")
    require(contract["limits"]["max_total_builder_calls"] == 4, "Unapproved attempt budget")
    require(contract["source"]["native_group_ids"] == [1], "Only group1 is approved")
    require(contract["deadline_approval_evidence"]["human_message_id"] == "01a16d43-ab1f-79c1-935e-db5bdd1935ed", "Missing time approval")
    if "model_event_fields" in contract["stage_input_envelope"]:
        require(contract["stage_input_envelope"]["model_event_fields"] == list(MODEL_EVENT_FIELDS), "Unapproved projection fields")
        evidence = contract.get("model_view_approval_evidence", {})
        require(evidence.get("status") == "verified_actual_human_reply" and evidence.get("human_message_id"), "Model-view human approval is missing")
    return min(approved, package)


def assets_for(contract):
    return {stage: {"prompt": (CODE / item["prompt"]).read_text(),
                    "schema": read_json(CODE / item["schema"])} for stage, item in contract["stages"].items()}


def source_first(source):
    destination = WORK / "human-source-first"
    destination.mkdir(exist_ok=True, mode=0o700)
    atomic_json(destination / "source.json", source)
    rows = []
    for event in source["archive"]["events"]:
        metadata = {key: value for key, value in event.items() if key != "content"}
        rows.append(f"<article><h2>{escape(event['event_id'])}</h2><details><summary>Origin and delivery evidence</summary><pre>{escape(json.dumps(metadata, ensure_ascii=False, indent=2))}</pre></details><pre>{escape(event['content'])}</pre></article>")
    text = ("<!doctype html><meta charset=utf-8><title>Group1 checkpoint2: source-first review</title>"
            "<style>body{max-width:1100px;margin:2rem auto;font:16px system-ui;padding:0 1rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}article{border-top:1px solid #ccc;margin:2rem 0}details{font-size:13px}</style>"
            "<h1>Independent source-unit annotation: group1 after traveler2</h1>"
            "<p>Read only this source before viewing any extraction or model verdict. Independently identify the atomic useful facts and historical acts, preserving speaker, time, status, condition and negation. Copy exact source quotes. Derived history is repetition of its linked origins, not independent evidence. Native final plans do not establish peer approval or a commit.</p>"
            f"<p>Archive SHA-256: {source['archive_sha256']}. Two blank JSON forms are supplied separately. No human annotations have been completed.</p>" + "".join(rows))
    (destination / "index.html").write_text(text)
    template = {"schema": "source_first_independent_units_v1", "archive_sha256": source["archive_sha256"],
                "checkpoint_id": source["archive"]["checkpoint_id"], "annotator_id": None,
                "completed": False, "viewed_automatic_outputs_before_source_units": None,
                "source_units": [], "unit_fields": ["unit_id", "text", "source_refs_event_id_and_exact_quote",
                    "speaker", "time_scope", "status", "conditions", "negation", "transfer_relevance", "harmful_or_benign_or_uncertain", "notes"],
                "instructions": "Annotate source units independently before extraction matching or V/P review. Leave unknown fields explicit. Preserve disagreements for adjudication; do not treat model output as gold."}
    for number in (1, 2):
        path = destination / f"annotator-{number}.blank.json"
        if not path.exists():
            atomic_json(path, {**template, "form_id": f"group1-cp2-annotator-{number}"})


def prepare(contract):
    smoke.admission(task_deadline(contract))
    source = build_source(NATIVE, UPSTREAM)
    require(source["native_scientific_fingerprint"] == SOURCE_SCIENCE, "Unexpected native source")
    assets = assets_for(contract)
    view = model_source_view(source["archive"], contract["stage_input_envelope"].get("model_event_fields"))
    manifest = {"scope": contract["scope"], "adapter_commit": os.environ["ADAPTER_COMMIT"],
                "source_archive_sha256": source["archive_sha256"], "source_scientific_fingerprint": SOURCE_SCIENCE,
                "contract_sha256": sha256(CONTRACT.read_bytes()).hexdigest(),
                "assets_sha256": fingerprint(assets), "generation": contract["model"]["generation"],
                "model_source_view_sha256": fingerprint(view),
                "model_source_version": view.get("source_version"),
                "limits": contract["limits"], "approval_evidence": contract["approval_evidence"],
                "deadline_approval_evidence": contract["deadline_approval_evidence"],
                "model_view_approval_evidence": contract.get("model_view_approval_evidence"),
                "model": read_json(smoke.CONFIG)["model"],
                "runtime_argv": smoke.server_command(read_json(smoke.CONFIG), ALIAS)}
    plan_sha = fingerprint(manifest)
    existing = WORK / "manifest.json"
    if existing.exists():
        require(read_json(existing) == {"plan": manifest, "plan_sha256": plan_sha}, "Frozen plan changed; no restart allowed")
    else:
        atomic_json(existing, {"plan": manifest, "plan_sha256": plan_sha})
    atomic_json(WORK / "source.json", source)
    atomic_json(WORK / "model-view.json", view)
    source_first(source)
    return source, assets, plan_sha


class BuilderClient:
    def __init__(self, contract, plan_sha, *, http, work=WORK, deadline=None):
        self.contract, self.plan_sha, self.http, self.work = contract, plan_sha, http, Path(work)
        self.deadline = task_deadline(contract) if deadline is None else deadline
        self.input_counts, self.total_tokens = [], 0

    def __call__(self, stage, messages):
        smoke.admission(self.deadline)
        requests_path, responses_path = self.work / "io/requests.jsonl", self.work / "io/responses.jsonl"
        requests, responses = smoke.journal_rows(requests_path), smoke.journal_rows(responses_path)
        require(len(requests) == len(responses), "Unresolved attempt: spend unknown, no retry allowed")
        logical = f"memory/group-1/checkpoint-2/{stage}"
        require(len(requests) < 4 and all(row["logical_call_id"] != logical for row in requests), "Attempt already spent or total budget exhausted")
        require(stage == ("S", "C", "V", "P")[len(requests)], "Out-of-order builder stage")
        specification = self.contract["stages"][stage]
        payload = {"model": ALIAS, "messages": messages, **self.contract["model"]["generation"],
                   "max_tokens": specification["max_output_tokens"], "stream": False}
        # No tools, actor policy override, guided decoding, SDK retries or repair calls.
        token_request = {key: payload[key] for key in ("model", "messages", "chat_template_kwargs")}
        timeout = min(900, self.deadline - time.time())
        require(timeout > 0, "Task deadline reached before tokenization")
        token_response = self.http.post(smoke.URL + "/tokenize", json=token_request, timeout=timeout)
        token_response.raise_for_status()
        tokenized = token_response.json()
        count = tokenized["count"]
        require(type(count) is int and tokenized["max_model_len"] == 32768, "Unexpected rendered context count")
        require(count <= self.contract["limits"]["input_caps"][stage] and count + payload["max_tokens"] <= 32768,
                f"{stage}: full rendered input {count} plus output reserve exceeds approved context; no truncation")
        require(self.total_tokens + count + payload["max_tokens"] <= 131072, "Checkpoint token budget exhausted")
        smoke.admission(self.deadline)
        timeout = min(900, self.deadline - time.time())
        require(timeout > 0, "Task deadline reached before model attempt")
        atomic_json(self.work / "current-job.json", {"stage": stage, "deadline_unix": min(self.deadline, time.time() + timeout)})
        body = canonical(payload)
        request = {"logical_call_id": logical, "attempt": 1, "stage": stage, "timestamp": utc(),
                   "plan_sha256": self.plan_sha, "request_sha256": sha256(body).hexdigest(),
                   "body_utf8": body.decode(), "rendered_input_tokens": count, "timeout_seconds": timeout}
        append_event(requests_path, request)
        started = time.monotonic()
        try:
            response = self.http.post(smoke.URL + "/v1/chat/completions", content=body,
                                      headers={"Content-Type": "application/json"}, timeout=timeout)
        except BaseException:
            append_event(self.work / "io/transport-errors.jsonl", {"logical_call_id": logical, "timestamp": utc(), "traceback": traceback.format_exc(), "spend": "unknown_no_retry"})
            raise
        append_event(responses_path, {"logical_call_id": logical, "request_sha256": request["request_sha256"],
                     "timestamp": utc(), "duration_seconds": time.monotonic() - started,
                     "status_code": response.status_code, "body_utf8": response.text})
        response.raise_for_status()
        raw = strict_json(response.text)
        require(len(raw["choices"]) == 1 and raw["choices"][0]["finish_reason"] == "stop", "Builder completion incomplete or multiple choices")
        message = raw["choices"][0]["message"]
        require(not message.get("tool_calls"), "Unexpected tool call from memory builder")
        require(not message.get("reasoning") and not message.get("reasoning_content"), "Thinking was not disabled")
        require(isinstance(message.get("content"), str) and message["content"].strip(), "Builder returned no JSON text")
        usage = raw["usage"]
        require(usage["prompt_tokens"] == count and usage["completion_tokens"] <= payload["max_tokens"], "Usage differs from approved rendered token budget")
        require((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) == 0, "Nonzero reasoning tokens")
        self.input_counts.append(count)
        self.total_tokens += count + usage["completion_tokens"]
        atomic_json(self.work / "usage.json", {"rendered_input_counts": self.input_counts, "input_plus_output_tokens": self.total_tokens})
        smoke.admission(self.deadline)
        return message["content"]


def tokenizer():
    return AutoTokenizer.from_pretrained("/model", local_files_only=True)


def report(work=WORK):
    work = Path(work)
    requests, responses = smoke.journal_rows(work / "io/requests.jsonl"), smoke.journal_rows(work / "io/responses.jsonl")
    packages = {path.stem: {key: value for key, value in read_json(path).items() if key not in {"content", "wrapped"}}
                for path in (work / "automatic-withheld/packages").glob("*.json")}
    statuses = {arm: packages[arm]["status"] if arm in packages else "not_built" for arm in ("F", "R", "S", "C", "CV", "CVP")}
    current_path = work / "automatic-withheld/stages/current.json"
    current = read_json(current_path)["stage"] if current_path.exists() else None
    affected = {"S": ["S", "C", "CV", "CVP"], "C": ["C", "CV", "CVP"], "V": ["CV", "CVP"], "P": ["CVP"]}.get(current, [])
    for index, arm in enumerate(affected):
        if arm not in packages:
            statuses[arm] = "package_build_failure" if index == 0 else "not_built_due_prior_stage_failure"
    return {"scope": "native_group1_checkpoint2_memory_prototype_only", "finished_at": utc(),
            "model_attempts": len(requests), "received_responses": len(responses), "max_model_attempts": 4,
            "unresolved_attempts": len(requests) - len(responses), "retries": 0,
            "source_archive_sha256": read_json(work / "source.json")["archive_sha256"],
            "plan_sha256": read_json(work / "manifest.json")["plan_sha256"],
            "packages": packages, "package_statuses": statuses, "human_completed_forms": 0, "human_metrics": "pending_independent_source_first_annotation",
            "P_harmful_pass": {"value": None, "denominator": None, "reason": "Source-unit human labels not collected; report N/A if adjudicated denominator is zero"},
            "groups2_12_started": False, "two_agent_dev_started": False, "main_started": False}


def client(contract):
    source, assets, plan_sha = prepare(contract)
    require(not (WORK / "io/requests.jsonl").exists(), "Existing attempt ledger: cache audit only; do not re-run")
    tokens = tokenizer()
    count_tokens = lambda text: len(tokens.encode(text, add_special_tokens=False))
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as http:
            builder = BuilderClient(contract, plan_sha, http=http)
            result = build_packages(source, contract, assets, builder, count_tokens,
                                    lambda name, value: atomic_json(WORK / "automatic-withheld" / name, value))
        value = {**report(), "status": "built_needs_independent_human_annotation", "summary": result}
        atomic_json(WORK / "report.json", value)
        atomic_json(WORK / "status.json", {"status": value["status"], "scope": contract["scope"], "model_attempts": value["model_attempts"]})
    except BaseException:
        value = {**report(), "status": "stopped_needs_review", "traceback": traceback.format_exc()}
        atomic_json(WORK / "report.json", value)
        atomic_json(WORK / "status.json", value)
        raise


def preflight(contract):
    source, assets, plan_sha = prepare(contract)
    tokens = tokenizer()
    counts = {}
    for stage in ("S", "C"):
        messages = stage_messages(stage, source["archive"], source["archive_sha256"], [], assets[stage]["schema"], assets[stage]["prompt"],
                                  event_fields=contract["stage_input_envelope"].get("model_event_fields"))
        encoded = tokens.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                             enable_thinking=False, return_dict=False)
        require(isinstance(encoded, list) and all(type(token) is int for token in encoded), "Unexpected preliminary tokenizer return type")
        counts[stage] = len(encoded)
    # Diagnostic projections are measured only, never supplied to an actual builder.
    diagnostic_views = {}
    minimal_keys = {"event_id", "role", "event_type", "group_id", "traveler_idx", "turn_idx", "order",
                    "content", "content_sha256", "origin_event_id", "derived_from"}
    for name in ("without_delivery_refs_only", "compact_proposed_unapproved"):
        events = [{key: value for key, value in event.items()
                   if (key != "delivery_refs" if name == "without_delivery_refs_only" else key in minimal_keys)}
                  for event in source["archive"]["events"]]
        diagnostic_views[name] = {}
        for stage in ("S", "C"):
            diagnostic_archive = {**source["archive"], "events": events}
            # The measured compact view includes the shared pinned version once.
            if name == "compact_proposed_unapproved":
                diagnostic_archive = source["archive"]
            messages = stage_messages(stage, diagnostic_archive, source["archive_sha256"], [], assets[stage]["schema"], assets[stage]["prompt"],
                event_fields=list(MODEL_EVENT_FIELDS) if name == "compact_proposed_unapproved" else None)
            diagnostic_views[name][stage] = len(tokens.apply_chat_template(messages, tokenize=True,
                add_generation_prompt=True, enable_thinking=False, return_dict=False))
    atomic_json(WORK / "input-diagnostics.json", {"actual_model_view_counts": counts,
                "actual_model_event_fields": contract["stage_input_envelope"].get("model_event_fields", "all_source_event_fields"),
                "not_authorized_for_inference_diagnostic_views": diagnostic_views,
                "source_events": len(source["archive"]["events"]), "source_archive_sha256": source["archive_sha256"],
                "content_removed": False, "events_removed": False, "new_model_calls": 0})
    for stage in ("S", "C"):
        require(counts[stage] <= contract["limits"]["input_caps"][stage], f"CPU preflight {stage} full input overflow: {counts[stage]}")
    atomic_json(WORK / "preflight.json", {"status": "passed_no_model_calls", "plan_sha256": plan_sha,
                "source_events": len(source["archive"]["events"]), "prefix_model_attempts": source["prefix_model_attempts"],
                "preliminary_rendered_counts": counts, "model_calls": 0,
                "note": "The actual server /tokenize admission check remains mandatory before every builder attempt"})


def cache_audit(contract):
    """Reconstruct using cached JSON responses; there is no inference client here."""
    completed = Path("/completed")
    source = read_json(completed / "source.json")
    require(source == build_source(NATIVE, UPSTREAM), "Source archive changed")
    manifest = read_json(completed / "manifest.json")
    require(fingerprint(manifest["plan"]) == manifest["plan_sha256"], "Plan hash mismatch")
    require(manifest["plan"]["contract_sha256"] == sha256(CONTRACT.read_bytes()).hexdigest(), "Contract changed")
    assets = assets_for(contract)
    require(manifest["plan"]["assets_sha256"] == fingerprint(assets), "Prompts or schemas changed")
    view = model_source_view(source["archive"], contract["stage_input_envelope"].get("model_event_fields"))
    require(view == read_json(completed / "model-view.json") and fingerprint(view) == manifest["plan"]["model_source_view_sha256"], "Model source view changed")
    streams = []
    for name in ("requests", "responses"):
        path = completed / f"io/{name}.jsonl"
        content = path.read_bytes() if path.exists() else b""
        require(not content or content.endswith(b"\n"), "Truncated durable journal; do not infer zero spend")
        streams.append([strict_json(line) for line in content.decode().splitlines()])
    requests, responses = streams
    require(len(requests) <= 4, "Four-attempt ceiling violated")
    require(len({row["logical_call_id"] for row in requests}) == len(requests), "Repeated logical attempt")
    require([row["stage"] for row in requests] == list(("S", "C", "V", "P")[:len(requests)]), "Unexpected stage order")
    by_id = {row["logical_call_id"]: row for row in responses}
    require(len(by_id) == len(responses), "Duplicate response receipt")
    for request in requests:
        payload = strict_json(request["body_utf8"])
        require(sha256(request["body_utf8"].encode()).hexdigest() == request["request_sha256"], "Wire body hash mismatch")
        require(request["plan_sha256"] == manifest["plan_sha256"] and request["attempt"] == 1, "Attempt identity differs")
        expected = {"model": ALIAS, "messages": payload["messages"], **contract["model"]["generation"],
                    "max_tokens": contract["stages"][request["stage"]]["max_output_tokens"], "stream": False}
        require(payload == expected, "Actual wire profile changed or includes extra tools/options")
        require(request["rendered_input_tokens"] <= contract["limits"]["input_caps"][request["stage"]], "Input cap violated")
    index = 0

    def cached(stage, messages):
        nonlocal index
        require(index < len(requests), "No cached next attempt")
        request = requests[index]
        index += 1
        require(request["stage"] == stage and strict_json(request["body_utf8"])["messages"] == messages, "Replay input differs from actual wire")
        require(request["logical_call_id"] in by_id, "Cached attempt unresolved; spend unknown")
        response = by_id[request["logical_call_id"]]
        require(response["request_sha256"] == request["request_sha256"] and response["status_code"] == 200, "Cached response failed")
        raw = strict_json(response["body_utf8"])
        require(raw["usage"]["prompt_tokens"] == request["rendered_input_tokens"], "Cached actual input usage mismatch")
        return raw["choices"][0]["message"]["content"]

    replayed = {}
    tokens = tokenizer()
    failure = None
    try:
        build_packages(source, contract, assets, cached,
                       lambda text: len(tokens.encode(text, add_special_tokens=False)), replayed.__setitem__)
    except Exception as error:
        failure = {"type": type(error).__name__, "message": str(error)}
    original = read_json(completed / "report.json")
    require((failure is None) == (original["status"] == "built_needs_independent_human_annotation"), "Replay success/failure differs")
    require(index == len(requests), "Cached replay did not consume every admitted attempt")
    for name, value in replayed.items():
        if name == "stages/current.json":
            continue
        require(value == read_json(completed / "automatic-withheld" / name), f"Cached transform differs: {name}")
    atomic_json(WORK / "audit.json", {"status": "passed_cache_only", "scope": contract["scope"],
                "plan_sha256": manifest["plan_sha256"], "source_archive_sha256": source["archive_sha256"],
                "cached_attempts": len(requests), "replayed_artifacts": len(replayed), "reproduced_failure": failure,
                "new_model_calls": 0, "human_completed_forms": 0})


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    contract = read_json(CONTRACT)
    require(os.getuid() != 0, "Non-root Docker is required")
    with (WORK / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.audit:
            smoke.admission(task_deadline(contract))
            cache_audit(contract)
        elif args.preflight:
            preflight(contract)
        elif args.client:
            client(contract)
        else:
            require((WORK / "preflight.json").is_file(), "CPU preflight required")
            require(not (WORK / "io/requests.jsonl").exists(), "Attempt ledger already exists; no new model calls")
            # The client takes this same lock after the supervisor releases it.
            fcntl.flock(lock, fcntl.LOCK_UN)
            smoke.supervise(read_json(smoke.CONFIG), task_deadline(contract),
                            client_module="collusion_handoff.memory_runner", alias=ALIAS, scope=contract["scope"])


if __name__ == "__main__":
    main()
