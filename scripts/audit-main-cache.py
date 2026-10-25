"""Replay main actors and memory through frozen production code, without inference.

Execute in nonroot CPU Docker with network=none and immutable input mounts.
Protocol-invalid model actions remain replayable outcomes. Missing transport or
admission evidence fails this audit rather than inventing a model response.
"""

import argparse
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import traceback
from unittest.mock import patch


def admission_phase_stop(result):
    """Frozen production AdmissionFailure semantics, including budget exhaustion."""
    detail = result.get("failure") or {}
    if detail.get("type") != "AdmissionFailure":
        raise RuntimeError("No typed admission failure to replay")
    kind = detail["kind"]
    if kind not in {"budget_exhausted", "phase_stopped", "context_overflow", "timeout", "tool_error"}:
        raise RuntimeError("Unimplemented admission failure replay profile: " + str(kind))
    return kind in {"budget_exhausted", "phase_stopped"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "review", "inference-codepath", "prefix-preflight", "continuation-preflight", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model", type=Path, default=Path("/model"))
    args = parser.parse_args()
    if (os.getuid() == 0 or not Path("/.dockerenv").exists()
            or os.environ.get("CUDA_VISIBLE_DEVICES") != ""
            or list(Path("/dev").glob("nvidia[0-9]*"))
            or {p.name for p in Path("/sys/class/net").iterdir()} != {"lo"}):
        raise RuntimeError("Requires nonroot CPU Docker with no GPU and network=none")
    root, review, code, output = (p.resolve() for p in
        (args.run, args.review, args.inference_codepath, args.output))
    if output.exists() or root == output or root in output.parents:
        raise RuntimeError("Use a new audit output outside the immutable run")
    os.umask(0o077)
    sys.path.insert(0, str(code / "src"))
    import httpx
    from transformers import AutoTokenizer
    from collusion_handoff import abe_cycle, main_cycle, readiness_cycle
    from collusion_handoff.abe_runner import AdmissionFailure
    from collusion_handoff.artifacts import atomic_json, canonical, fingerprint, read_json
    from collusion_handoff.catalog import load_accommodations
    from collusion_handoff.memory import strict_json
    from collusion_handoff.memory_v4 import constrained_payload

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    def rows(path):
        raw = path.read_bytes()
        require(not raw or raw.endswith(b"\n"), "Truncated journal: " + str(path))
        return [strict_json(line) for line in raw.decode().splitlines() if line.strip()]

    manifest, config, base, assets, _ = main_cycle.approved_pipeline(review, code)
    require(read_json(root / "main-manifest.json") == manifest, "Run master differs from frozen main")
    prefix_preflight = read_json(args.prefix_preflight / "preflight.json")
    continuation_preflight = read_json(args.continuation_preflight / "preflight.json")
    for folder, pre, phase in ((args.prefix_preflight, prefix_preflight, "prefixes"),
                               (args.continuation_preflight, continuation_preflight, "continuations")):
        require(pre["manifest_sha256"] == fingerprint(manifest) and pre["phase"] == phase
                and pre["status"] == "passed_no_model_calls", "Phase CPU receipt belongs to another main")
        for name, digest in pre["files"].items():
            path = folder / name
            require(path.resolve().is_relative_to(folder.resolve()) and sha256(path.read_bytes()).hexdigest() == digest,
                    "Phase CPU artifact changed")
        for name, digest in pre["prepared_files_sha256"].items():
            require(Path(name).name == name and sha256((root / "cpu-prepare" / name).read_bytes()).hexdigest() == digest,
                    "Prepared registry/denominator changed")
        for name, digest in pre["review_files_sha256"].items():
            path = review / name
            require(not Path(name).is_absolute() and ".." not in Path(name).parts
                    and path.resolve().is_relative_to(review) and sha256(path.read_bytes()).hexdigest() == digest,
                    "Actual collection review packet changed")
        actual_phase = read_json(root / phase / "phase-manifest.json")
        require(actual_phase["manifest_sha256"] == fingerprint(manifest)
                and actual_phase["CPU_preflight_sha256"] == sha256((folder / "preflight.json").read_bytes()).hexdigest(),
                "Supplied CPU receipt differs from the receipt recorded by the actual inference phase")
    require(main_cycle.prefix_bundle(root / "prefixes", manifest["sample"]["group_ids"])
            == continuation_preflight["historical_prefix_bundle"], "Historical B/world/source bundle changed")
    requests, responses = rows(root / "ledger/requests.jsonl"), rows(root / "ledger/responses.jsonl")
    request_map = {r["logical_call_id"]: r for r in requests}
    response_map = {r["logical_call_id"]: r for r in responses}
    require(len(request_map) == len(requests) and len(response_map) == len(responses)
            and set(response_map) <= set(request_map), "Duplicate or orphan wire records")
    total, allocations = 0, {}
    unknown_spend = []
    for row in requests:
        identity = row["logical_call_id"]
        require(row["attempt"] == 1 and sha256(row["body_utf8"].encode()).hexdigest() == row["request_sha256"],
                "Request identity, attempt or wire hash differs")
        allocations[row["allocation"]] = allocations.get(row["allocation"], 0) + 1
        response = response_map.get(identity)
        if response is None or response.get("accounted_actual_tokens") is None:
            unknown_spend.append(identity)
            total += row["reserved_input_output_tokens"]
        else:
            require(response["request_sha256"] == row["request_sha256"], "Response request binding differs")
            body, usage = strict_json(row["body_utf8"]), strict_json(response["body_utf8"])["usage"]
            require(usage["prompt_tokens"] == row["rendered_input_tokens"]
                    and type(usage["completion_tokens"]) is int
                    and 0 <= usage["completion_tokens"] <= body["max_tokens"]
                    and response["accounted_actual_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"],
                    "Raw usage differs from accounted spend")
            total += response["accounted_actual_tokens"]
    require(len(requests) <= manifest["budget"]["max_model_attempts"]
            and total <= manifest["budget"]["max_input_output_tokens"]
            and all(k in config["allocations"] and n <= config["allocations"][k] for k, n in allocations.items()),
            "A frozen main budget was exceeded")
    output.mkdir(parents=True, mode=0o700)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    catalog = load_accommodations("/database/accommodations/clean_accommodations_2022.csv",
                                  config["task_policy"]["catalog_csv_sha256"])
    prepared = read_json(root / "cpu-prepare/private-task-registry.json")["groups"]
    regenerated, _ = main_cycle.prepare_registry(manifest, config,
        Path("/main-data") / config["main_data_source"]["filename"], catalog)
    require(main_cycle.registry_digest(regenerated) == main_cycle.registry_digest(prepared),
            "Prepared tasks differ from the pinned canonical dataset and prospective eligibility")
    budget = SimpleNamespace(ledger=root / "ledger", root=root)
    consumed, checks, errors = set(), {}, []

    def count(body):
        return len(tokenizer.apply_chat_template(body["messages"], tokenize=True,
            add_generation_prompt=True, enable_thinking=False, return_dict=False))

    def cached(body, identity, allocation, actual_job):
        require(identity in request_map and identity not in consumed, "Missing or duplicate cached generation: " + identity)
        request = request_map[identity]
        require(request["allocation"] == allocation and strict_json(request["body_utf8"]) == body,
                "Reconstructed request/allocation differs from actual wire: " + identity)
        rendered = count(body)
        require(rendered == request["rendered_input_tokens"]
                and rendered + body["max_tokens"] <= config["limits"]["context"], "Rendered context differs")
        require(request in rows(actual_job / "io/requests.jsonl"), "Per-job request differs from master")
        require(identity in response_map, "Missing response cannot be replayed as success")
        response = response_map[identity]
        require(response in rows(actual_job / "io/responses.jsonl") and not response.get("transport_error"),
                "Per-job response differs or requires separate transport-failure review")
        consumed.add(identity)
        return httpx.Response(response["status_code"], content=response["body_utf8"].encode(),
            request=httpx.Request("POST", "http://127.0.0.1:18083/v1/chat/completions"))

    def actor_factory(actual_job, expected_allocation):
        def factory(selected_config, selected_base, ignored_budget, replay_job, allocation, **kwargs):
            require(selected_config == config and selected_base == base and allocation == expected_allocation,
                    "Actor generation profile changed")
            def generate(role, messages, schema, identity, deadline, remaining):
                body = constrained_payload("qwen-native-dev", abe_cycle.actor_generation(base), messages,
                    schema, config["limits"]["actor_max_output_tokens"])
                if identity not in request_map:
                    task_id = identity.split("/")[1]
                    result = read_json(actual_job / "results" / (task_id + ".json"))
                    detail = result.get("failure") or {}
                    require(detail.get("type") == "AdmissionFailure", "No cache and no explicit admission failure")
                    raise AdmissionFailure(detail["message"], kind=detail["kind"],
                                           stop_phase=admission_phase_stop(result))
                if kwargs.get("expected_initial_request") is not None and not any(
                        key.startswith(identity.split("/")[0] + "/") for key in consumed):
                    require(body == kwargs["expected_initial_request"], "Initial wire differs from CPU probe")
                require(count(body) + body["max_tokens"] <= remaining, "Task token reservation differs")
                response = cached(body, identity, allocation, actual_job)
                text = readiness_cycle.content(response)
                usage = response.json()["usage"]
                return {"text": text, "input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"]}
            return generate
        return factory

    def compare_tree(actual, replay):
        def files(folder):
            return {str(p.relative_to(folder)) for p in folder.rglob("*")
                    if p.is_file() and "io" not in p.relative_to(folder).parts
                    and p.suffix in {".json", ".jsonl"}}
        expected, observed = files(actual), files(replay)
        require(expected == observed, "Artifact inventory differs: " + str(sorted(expected ^ observed)))
        for name in expected:
            left, right = actual / name, replay / name
            require((rows(left) == rows(right)) if left.suffix == ".jsonl" else read_json(left) == read_json(right),
                    "Replayed artifact differs: " + str(left))
        return len(expected)

    original_read = abe_cycle.read_json
    def read_with_wire_probe(path):
        return original_read(args.prefix_preflight / "actor-wire-probes.json") if str(path) == "/preflight/actor-wire-probes.json" else original_read(path)

    def check(name, action):
        try:
            checks[name] = action()
        except Exception:
            errors.append({"check": name, "traceback": traceback.format_exc()})

    for number in manifest["sample"]["group_ids"]:
        actual_prefix = root / f"prefixes/prefix/group-{number}"
        def prefix(number=number, actual_prefix=actual_prefix):
            registry = read_json(actual_prefix / "private-registry.json")
            require(registry == prepared[str(number)], "Actual prefix task input differs from its CPU registry")
            destination = output / "prefix-replay"
            with patch.object(abe_cycle, "actor_generator", actor_factory(actual_prefix, "prefix_actors")), \
                    patch.object(abe_cycle, "read_json", read_with_wire_probe):
                replay = abe_cycle.prefix_group(deepcopy(registry), catalog, config, base, budget, destination,
                    induced=manifest["sample"]["prefix_condition_by_group"][str(number)] == "private_goal_induction",
                    allocation="prefix_actors")
            return {"artifacts_compared": compare_tree(actual_prefix, replay)}
        check("prefix-" + str(number), prefix)

    groups = {n: read_json(root / f"prefixes/prefix/group-{n}/private-registry-bound.json")
              for n in manifest["sample"]["group_ids"]}
    check("source-review-locks", lambda: {"schema": main_cycle.verify_annotations(review, manifest, groups,
                                                                         root / "prefixes")["schema"]})
    for number, registry in groups.items():
        def continuations(number=number, registry=registry):
            actual_memory = root / f"continuations/memory/group-{number}"
            replay_memory = output / f"memory-replay/group-{number}"
            require(read_json(actual_memory / "source.json") == read_json(root / f"prefixes/prefix/group-{number}/source.json"),
                    "Memory builder source differs from the actual prefix archive")
            def memory_dispatch(ignored, body, identity, allocation, job):
                require(allocation == "memory_builders", "Unexpected memory allocation")
                return cached(body, identity, allocation, actual_memory)
            with patch.object(readiness_cycle, "dispatch", memory_dispatch):
                readiness_cycle.memory_job(read_json(actual_memory / "source.json"), replay_memory,
                    f"continuations/group-{number}", config, assets, budget,
                    lambda text: len(tokenizer.encode(text, add_special_tokens=False)), allocation="memory_builders")
            memory_count = compare_tree(actual_memory, replay_memory)
            packages = {p.stem: read_json(p) for p in (replay_memory / "automatic-withheld/packages").glob("*.json")}
            actual = root / f"continuations/continuations/group-{number}"
            def factory(selected_config, selected_base, ignored_budget, job, allocation, **kwargs):
                return actor_factory(actual / job.name, "continuation_actors")(
                    selected_config, selected_base, ignored_budget, job, allocation, **kwargs)
            replay = main_cycle.continuation_group(root / f"prefixes/prefix/group-{number}", packages,
                manifest, config, base, budget, output / "continuation-replay", group_registry=deepcopy(registry),
                catalog=catalog, generator_factory=factory)
            return {"memory_artifacts_compared": memory_count, "actor_artifacts_compared": compare_tree(actual, replay)}
        check("continuations-" + str(number), continuations)

    def matrix():
        assigned = read_json(root / "cpu-prepare/assigned-future-opportunities.json")
        require(assigned == main_cycle.assigned_opportunities(manifest, groups), "Frozen denominator differs")
        aggregate = read_json(root / "continuations/all-assigned-results.json")
        require(aggregate["assigned"] == len(assigned) == len(aggregate["observed"]), "Assigned coverage differs")
        key = lambda row: (row["group_id"], row["arm"], row["task_id"])
        expected = {key(row) for row in assigned}
        require(len({key(row) for row in aggregate["observed"]}) == len(expected)
                and {key(row) for row in aggregate["observed"]} == expected, "Duplicate/unassigned aggregate result")
        for row in aggregate["observed"]:
            branch = abe_cycle.namespace(config["run_id"], row["group_id"], "recipient/" + row["arm"])
            result = root / f"continuations/continuations/group-{row['group_id']}" / branch / "results" / (row["task_id"] + ".json")
            require(read_json(result) == row["result"], "Aggregate differs from replayed branch result")
        return {"assigned": len(assigned)}
    check("assigned-matrix", matrix)
    if consumed != set(request_map):
        errors.append({"check": "exact_cache_consumption", "unconsumed": sorted(set(request_map) - consumed),
                       "extra": sorted(consumed - set(request_map))})
    if unknown_spend:
        errors.append({"check": "accounting", "unknown_spend_requests": unknown_spend})
    analysis_inputs = [root / "main-manifest.json", root / "ledger/requests.jsonl", root / "ledger/responses.jsonl",
        root / "prefixes/phase-manifest.json", root / "continuations/phase-manifest.json",
        root / "cpu-prepare/assigned-future-opportunities.json", root / "continuations/all-assigned-results.json"]
    for number, group in groups.items():
        prefix = root / f"prefixes/prefix/group-{number}"
        analysis_inputs.extend(prefix / name for name in ("source.json", "snapshot.json", "private-registry-bound.json"))
        analysis_inputs.extend(prefix / "results" / (task["task_id"] + ".json") for task in group["tasks"][:2])
    report = {"schema": "main_cache_replay_v1", "status": "passed_cache_only" if not errors else "failed",
        "manifest_sha256": fingerprint(manifest), "inference_code_commit": manifest["pipeline"]["code_commit"],
        "audit_code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "new_model_calls": 0, "no_HTTP_client_constructed": True, "human_gold": False,
        "requests": len(requests), "calls_replayed": len(consumed), "accounted_tokens": total,
        "allocations": allocations, "checks": checks, "errors": errors,
        "review_files_sha256": continuation_preflight["review_files_sha256"],
        "input_sha256": {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in analysis_inputs}}
    atomic_json(output / "audit.json", report)
    print(canonical({"status": report["status"], "calls_replayed": len(consumed), "errors": len(errors)}).decode())
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
