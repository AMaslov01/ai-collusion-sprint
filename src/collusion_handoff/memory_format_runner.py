"""Versioned cached-S recovery and at most three remaining group1 builder calls."""

import argparse
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import time
import traceback

import httpx

from . import memory_runner as original
from . import smoke
from .artifacts import atomic_json, fingerprint, read_json
from .memory import build_packages, pack_summary, require, stage_messages, strict_json, validate
from .memory_format import VERSION, decode_format
from .memory_source import build_source


WORK = Path("/work")
BASE = Path("/basecode")
PARENT = Path("/v1")
V2_CONFIG = Path("/code/configs/memory-group1-format-v2.json")


def inputs():
    variant = read_json(V2_CONFIG)
    require(variant["status"] == "human_approved", "Decoder variant requires actual human approval")
    require(variant["decoder_version"] == VERSION and variant["max_new_attempts"] == 3 and variant["max_cumulative_attempts"] == 4, "Decoder or attempt budget changed")
    require(variant["human_approval_evidence"]["human_message_id"] == "01a16d6c-5d1e-70f1-86fa-861d58ae2be8", "Missing human decoder approval")
    base = read_json(BASE / "configs/memory-group1.json")
    deadline = original.task_deadline(base)
    smoke.admission(deadline)
    parent_manifest = read_json(PARENT / "manifest.json")
    require(parent_manifest["plan_sha256"] == variant["parent_plan_sha256"], "Wrong parent plan")
    require(fingerprint(parent_manifest["plan"]) == parent_manifest["plan_sha256"], "Parent manifest hash mismatch")
    require(sha256((BASE / "configs/memory-group1.json").read_bytes()).hexdigest() == parent_manifest["plan"]["contract_sha256"], "Frozen base contract changed")
    require(variant["task_deadline_utc"] == base["limits"]["task_deadline_utc"], "Variant deadline changed")
    for filename, key in (("report.json", "parent_failure_report_sha256"),
                          ("io/requests.jsonl", "parent_request_ledger_sha256"),
                          ("io/responses.jsonl", "parent_response_ledger_sha256")):
        require(sha256((PARENT / filename).read_bytes()).hexdigest() == variant[key], "Frozen parent bytes changed: " + filename)
    requests, responses = smoke.journal_rows(PARENT / "io/requests.jsonl"), smoke.journal_rows(PARENT / "io/responses.jsonl")
    require(len(requests) == len(responses) == 1 and requests[0]["stage"] == "S", "Parent must contain exactly one resolved S attempt")
    raw = strict_json(responses[0]["body_utf8"])
    require(responses[0]["status_code"] == 200 and raw["choices"][0]["finish_reason"] == "stop", "Parent S completion was not complete")
    assets = {stage: {"prompt": (BASE / spec["prompt"]).read_text(), "schema": read_json(BASE / spec["schema"])}
              for stage, spec in base["stages"].items()}
    require(fingerprint(assets) == parent_manifest["plan"]["assets_sha256"], "Frozen prompts or schemas changed")
    text = raw["choices"][0]["message"]["content"]
    try:
        validate(text, assets["S"]["schema"])
    except ValueError as error:
        strict_failure = type(error).__name__
    else:
        raise RuntimeError("Expected the preserved original strict S failure")
    require(strict_failure == "JSONDecodeError", "Original failure no longer reproduces")
    source = read_json(PARENT / "source.json")
    require(source == build_source(original.NATIVE, original.UPSTREAM), "Native prefix source changed")
    require(source["archive_sha256"] == parent_manifest["plan"]["source_archive_sha256"], "Source identity changed")
    return variant, base, deadline, parent_manifest, assets, source, raw, strict_failure


def prepare():
    variant, base, deadline, parent_manifest, assets, source, raw, strict_failure = inputs()
    plan = {"scope": variant["scope"], "variant": VERSION, "adapter_commit": os.environ["ADAPTER_COMMIT"],
            "variant_contract_sha256": sha256(V2_CONFIG.read_bytes()).hexdigest(),
            "decoder_source_sha256": sha256(Path("/code/src/collusion_handoff/memory_format.py").read_bytes()).hexdigest(),
            "base_code_commit": variant["base_code_commit"], "base_contract_sha256": sha256((BASE / "configs/memory-group1.json").read_bytes()).hexdigest(),
            "parent_plan_sha256": parent_manifest["plan_sha256"],
            "parent_failure_report_sha256": variant["parent_failure_report_sha256"],
            "parent_request_ledger_sha256": variant["parent_request_ledger_sha256"],
            "parent_response_ledger_sha256": variant["parent_response_ledger_sha256"],
            "source_archive_sha256": source["archive_sha256"],
            "model_source_view_sha256": parent_manifest["plan"]["model_source_view_sha256"],
            "assets_sha256": fingerprint(assets), "generation": base["model"]["generation"],
            "limits": base["limits"], "human_approval_evidence": variant["human_approval_evidence"],
            "already_spent_attempts": 1, "max_new_attempts": 3, "max_cumulative_attempts": 4,
            "parent_strict_failure": strict_failure, "parent_original_usage": raw["usage"]}
    manifest = {"plan": plan, "plan_sha256": fingerprint(plan)}
    if (WORK / "manifest.json").exists():
        require(read_json(WORK / "manifest.json") == manifest, "Frozen continuation plan changed")
    else:
        atomic_json(WORK / "manifest.json", manifest)
    atomic_json(WORK / "source.json", source)
    view = read_json(PARENT / "model-view.json")
    require(fingerprint(view) == parent_manifest["plan"]["model_source_view_sha256"], "Frozen model view changed")
    atomic_json(WORK / "model-view.json", view)
    return variant, base, deadline, assets, source, raw, manifest


def seed_parent_ledger():
    directory = WORK / "io"
    directory.mkdir(mode=0o700, exist_ok=True)
    for name in ("requests", "responses"):
        target = directory / f"{name}.jsonl"
        content = (PARENT / "io" / f"{name}.jsonl").read_bytes()
        if target.exists():
            require(target.read_bytes() == content, "Continuation already spent an attempt; cache audit only")
        else:
            with target.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def decoder(persist):
    def decode(stage, text, schema):
        value, receipt = decode_format(text, schema)
        persist(f"stages/{stage}-decoding.json", {**receipt, "stage": stage, "cached_from_v1": stage == "S"})
        return value
    return decode


def preflight():
    variant, base, deadline, assets, source, raw, manifest = prepare()
    tokens = original.tokenizer()
    recovered, receipt = decode_format(raw["choices"][0]["message"]["content"], assets["S"]["schema"])
    summary, excluded = pack_summary(recovered["sentences"], lambda text: len(tokens.encode(text, add_special_tokens=False)))
    messages = stage_messages("C", source["archive"], source["archive_sha256"], [], assets["C"]["schema"], assets["C"]["prompt"],
                              event_fields=base["stage_input_envelope"]["model_event_fields"])
    encoded = tokens.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False)
    require(isinstance(encoded, list) and all(type(token) is int for token in encoded), "Unexpected tokenization return type")
    require(len(encoded) <= base["limits"]["input_caps"]["C"], "Continuation C input exceeds the original context cap")
    atomic_json(WORK / "preflight.json", {"status": "passed_no_model_calls", "plan_sha256": manifest["plan_sha256"],
                "original_strict_failure": manifest["plan"]["parent_strict_failure"], "cached_format_recovery": receipt,
                "cached_S_content_tokens": len(tokens.encode(summary, add_special_tokens=False)),
                "cached_S_excluded_sentences": len(excluded), "C_preliminary_input_tokens": len(encoded),
                "already_spent_attempts": 1, "max_new_attempts": 3, "new_model_calls": 0})


def outcome(manifest):
    return {**original.report(), "scope": "native_group1_cp2_cached_format_recovery_and_remaining_stages_only",
            "variant": VERSION, "parent_v1_status": "stopped_needs_review",
            "parent_v1_report_sha256": manifest["plan"]["parent_failure_report_sha256"],
            "cached_stage_reused": "S", "new_model_attempts": len(smoke.journal_rows(WORK / "io/requests.jsonl")) - 1,
            "max_new_model_attempts": 3, "original_v1_accepted_or_rewritten": False}


def client():
    variant, base, deadline, assets, source, raw, manifest = prepare()
    seed_parent_ledger()
    tokens = original.tokenizer()
    persist = lambda name, value: atomic_json(WORK / "automatic-withheld" / name, value)
    cached_s_used = False
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as http:
            builder = original.BuilderClient(base, manifest["plan_sha256"], http=http, work=WORK, deadline=deadline)
            builder.input_counts = [raw["usage"]["prompt_tokens"]]
            builder.total_tokens = raw["usage"]["prompt_tokens"] + raw["usage"]["completion_tokens"]

            def generate(stage, messages):
                nonlocal cached_s_used
                if stage == "S":
                    require(not cached_s_used, "Cached S requested twice")
                    cached_s_used = True
                    parent_request = strict_json(smoke.journal_rows(PARENT / "io/requests.jsonl")[0]["body_utf8"])
                    require(messages == parent_request["messages"], "Cached S input changed")
                    return raw["choices"][0]["message"]["content"]
                return builder(stage, messages)

            summary = build_packages(source, base, assets, generate, lambda text: len(tokens.encode(text, add_special_tokens=False)),
                                     persist, decode=decoder(persist))
        result = {**outcome(manifest), "status": "built_format_recovery_variant_needs_independent_human_annotation", "summary": summary}
        atomic_json(WORK / "report.json", result)
        atomic_json(WORK / "status.json", {"status": result["status"], "new_model_attempts": result["new_model_attempts"], "cumulative_model_attempts": result["model_attempts"]})
    except BaseException:
        result = {**outcome(manifest), "status": "stopped_needs_review", "traceback": traceback.format_exc()}
        atomic_json(WORK / "report.json", result)
        atomic_json(WORK / "status.json", result)
        raise


def audit():
    variant, base, deadline, parent_manifest, assets, source, raw_s, strict_failure = inputs()
    completed = Path("/completed")
    manifest = read_json(completed / "manifest.json")
    require(fingerprint(manifest["plan"]) == manifest["plan_sha256"], "Continuation plan hash mismatch")
    require(manifest["plan"]["variant_contract_sha256"] == sha256(V2_CONFIG.read_bytes()).hexdigest(), "Variant contract changed")
    require(manifest["plan"]["decoder_source_sha256"] == sha256(Path("/code/src/collusion_handoff/memory_format.py").read_bytes()).hexdigest(), "Decoder changed")
    requests, responses = [], []
    for name, output in (("requests", requests), ("responses", responses)):
        data = (completed / f"io/{name}.jsonl").read_bytes()
        parent_bytes = (PARENT / f"io/{name}.jsonl").read_bytes()
        require(data.startswith(parent_bytes) and data.endswith(b"\n"), "Parent ledger prefix changed or tail truncated")
        output.extend(strict_json(line) for line in data.decode().splitlines())
    require(1 <= len(requests) <= 4 and len(requests) == len(responses), "Invalid cumulative attempt accounting")
    require([row["stage"] for row in requests] == list(("S", "C", "V", "P")[:len(requests)]), "Stage repeated or out of order")
    require(len({row["logical_call_id"] for row in requests}) == len(requests), "Repeated logical attempt")
    for index, (request, response) in enumerate(zip(requests, responses)):
        payload = strict_json(request["body_utf8"])
        expected = {"model": original.ALIAS, "messages": payload["messages"], **base["model"]["generation"],
                    "max_tokens": base["stages"][request["stage"]]["max_output_tokens"], "stream": False}
        require(payload == expected and sha256(request["body_utf8"].encode()).hexdigest() == request["request_sha256"], "Wire profile or body hash changed")
        require(request["attempt"] == 1 and request["plan_sha256"] == (parent_manifest["plan_sha256"] if index == 0 else manifest["plan_sha256"]), "Parent/new attempt identity changed")
        require(response["logical_call_id"] == request["logical_call_id"] and response["request_sha256"] == request["request_sha256"], "Response link mismatch")
    index = 0
    replayed = {}

    def cached(stage, messages):
        nonlocal index
        require(index < len(requests), "No cached next attempt")
        request, response = requests[index], responses[index]
        index += 1
        require(request["stage"] == stage and strict_json(request["body_utf8"])["messages"] == messages, "Replayed input differs")
        require(response["status_code"] == 200, "Cached HTTP failure")
        raw = strict_json(response["body_utf8"])
        require(raw["usage"]["prompt_tokens"] == request["rendered_input_tokens"], "Actual input count changed")
        return raw["choices"][0]["message"]["content"]

    tokens = original.tokenizer()
    failure = None
    try:
        build_packages(source, base, assets, cached, lambda text: len(tokens.encode(text, add_special_tokens=False)),
                       replayed.__setitem__, decode=decoder(replayed.__setitem__))
    except Exception as error:
        failure = {"type": type(error).__name__, "message": str(error)}
    actual = read_json(completed / "report.json")
    require((failure is None) == (actual["status"] == "built_format_recovery_variant_needs_independent_human_annotation"), "Replay success/failure differs")
    require(index == len(requests), "Not all cumulative attempts replayed")
    for name, value in replayed.items():
        if name != "stages/current.json":
            require(value == read_json(completed / "automatic-withheld" / name), "Replay artifact differs: " + name)
    atomic_json(WORK / "audit.json", {"status": "passed_cache_only", "variant": VERSION,
                "parent_strict_failure_reproduced": strict_failure, "cached_S_format_recovery_reproduced": True,
                "parent_v1_failure_report_sha256": variant["parent_failure_report_sha256"],
                "cumulative_attempts": len(requests), "new_attempts_in_variant": len(requests) - 1,
                "new_model_calls_by_audit": 0, "replayed_artifacts": len(replayed), "reproduced_variant_failure": failure,
                "plan_sha256": manifest["plan_sha256"], "source_archive_sha256": source["archive_sha256"]})


def main():
    os.umask(0o077)
    require(os.getuid() != 0, "Non-root Docker required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    with (WORK / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.preflight:
            preflight()
        elif args.audit:
            audit()
        elif args.client:
            client()
        else:
            require((WORK / "preflight.json").exists() and not (WORK / "io").exists(), "Fresh approved preflight required; never restart spent continuation")
            _, base, deadline, _, _, _, _ = prepare()
            fcntl.flock(lock, fcntl.LOCK_UN)
            smoke.supervise(read_json(BASE / "configs/dev-package.draft.json"), deadline,
                            client_module="collusion_handoff.memory_format_runner", alias=original.ALIAS,
                            scope="native_group1_cp2_cached_format_recovery_and_remaining_stages_only")


if __name__ == "__main__":
    main()
