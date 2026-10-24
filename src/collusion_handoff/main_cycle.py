"""Main orchestration with explicit inputs and separately verified human gates.

There is no default main configuration, sample, resource or authorization. The
validate command reads only the manifest. Other commands require the complete
approval packet before opening native data. DEV entry points remain unchanged.
"""

import argparse
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import traceback

from .artifacts import atomic_json, fingerprint, read_json
from .main_manifest import HUMAN_ID, SHA, ManifestError, load_manifest, verify_approval


ARMS = ("F", "S", "C", "CV", "CVP", "R")


def require(condition, message):
    if not condition:
        raise ManifestError(message)


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def registry_digest(groups):
    """Normalize IDs before hashing, including across a JSON write/read roundtrip."""
    normalized = {}
    for number, group in groups.items():
        require(type(number) in {int, str} and str(number).isdigit() and
                str(int(number)) == str(number), "Registry IDs must be canonical nonnegative integers")
        key = str(number)
        require(key not in normalized, "Duplicate normalized registry ID")
        normalized[key] = group
    return fingerprint(normalized)


def assigned_opportunities(manifest, groups):
    """The analysis population never changes the execution denominator."""
    sample = manifest["sample"]
    return [{"group_id": number, "arm": arm, "traveler_index": index,
             "task_id": groups[number]["tasks"][index - 1]["task_id"],
             "prefix_condition": sample["prefix_condition_by_group"][str(number)],
             "status": "assigned_before_prefix_outcomes"}
            for number in sample["group_ids"] for arm in sample["arms"]
            for index in sample["future_task_indices"]]


def approved_pipeline(review, code):
    """Check the approval first; reading this function cannot read native data."""
    review, code = Path(review), Path(code)
    manifest = load_manifest(review / "main-manifest.json")
    authority = verify_approval(manifest, read_json(review / "human-main-approval.json"),
                                read_json(review / "main-readiness.json"))
    pipeline = manifest["pipeline"]
    require(os.environ.get("ADAPTER_COMMIT") == pipeline["code_commit"], "Main checkout differs from approval")
    for name, digest in pipeline["file_sha256"].items():
        path = code / name
        require(path.resolve().is_relative_to(code.resolve()) and path.is_file(), "Invalid pinned pipeline file")
        require(file_hash(path) == digest, "Pinned main file changed: " + name)
    config_path = code / pipeline["config_path"]
    require(file_hash(config_path) == pipeline["config_sha256"], "Main pipeline configuration changed")
    config = read_json(config_path)
    require(config["scope"] == "main_pipeline_configuration_v1" and config["run_id"] == manifest["run_id"],
            "A DEV configuration cannot be used as main")
    source = config["main_data_source"]
    require(set(source) == {"host_directory", "filename", "sha256"}, "Explicit main data source is required")
    require(Path(source["host_directory"]).is_absolute() and
            Path(source["filename"]).name == source["filename"] and source["filename"] not in {".", ".."}
            and SHA.fullmatch(source["sha256"]), "Invalid main source identity")
    base_name = config["base_runtime_config"]
    require(base_name in pipeline["file_sha256"], "Base runtime must be pinned in the main manifest")
    base = read_json(code / base_name)
    require(base["model"]["revision"] == pipeline["model_revision"] and
            base["runtime"]["image_digest"] == pipeline["image_digest"], "Main model/image differs from approval")
    assets = {}
    for stage, spec in config["memory"]["stages"].items():
        require(spec["prompt"] in pipeline["file_sha256"] and spec["schema"] in pipeline["file_sha256"],
                "Memory assets must be individually pinned")
        assets[stage] = {"prompt": (code / spec["prompt"]).read_text(), "schema": read_json(code / spec["schema"])}
    require(set(assets) == {"S", "C", "V", "P"} and fingerprint(assets) == pipeline["assets_sha256"],
            "Main memory assets differ from approval")
    for key in ("A_prompt", "B_prompt", "private_A_prompt", "private_B_prompt"):
        require(config["protocol"][key] in pipeline["file_sha256"], "Actor prompt is not pinned")
    analysis_path = config["analysis_code_path"]
    require(pipeline["file_sha256"].get(analysis_path) == manifest["analysis"]["analysis_code_sha256"],
            "Analysis implementation differs from the approved manifest")
    budget = manifest["budget"]
    expected = {"max_new_model_attempts": budget["max_model_attempts"],
                "max_input_output_tokens": budget["max_input_output_tokens"],
                "gpu_service_wall_seconds": budget["gpu_container_seconds"],
                "gpu_service_accounting": budget["gpu_service_accounting"],
                "absolute_cutoff_utc": budget["absolute_cutoff_utc"], "max_attempts_per_logical_request": 1}
    require(all(config["limits"].get(key) == value for key, value in expected.items()) and
            config["allocations"] == budget["allocations"], "Main budget differs from approval")
    hardware = manifest["hardware"]
    require(hardware["tensor_parallel_size"] == hardware["endpoint_count"] == 1,
            "This implementation supports the reviewed single-device serving topology only")
    require(base["smoke"]["tensor_parallel_size"] == 1 and config["limits"]["concurrency"] == 1,
            "Main serving topology changed")
    require(config["operations"]["gpu_uuid"] == hardware["gpu_uuids"][0] and
            config["operations"]["gpu_name"] == hardware["gpu_names"][0], "Main GPU differs from approval")
    for config_key, manifest_key in (("server", "host"), ("cpu_limit", "cpu_limit"),
                                     ("RAM_limit_GiB", "RAM_limit_GiB"), ("shm_GiB", "shm_GiB")):
        require(config["operations"][config_key] == hardware[manifest_key], "Main resource configuration changed")
    require(tuple(manifest["sample"]["arms"]) == ARMS, "Unsupported main arm matrix")
    return manifest, config, base, assets, authority


def prepare_registry(manifest, config, source_path, catalog):
    """Source-only eligibility, preserving all assigned IDs without replacement."""
    from .abe_tasks import prepare_group
    from .memory import strict_json
    source_path = Path(source_path)
    require(file_hash(source_path) == config["main_data_source"]["sha256"], "Main native source changed")
    selected = set(manifest["sample"]["group_ids"])
    rows = {}
    for line in source_path.read_text().splitlines():
        row = strict_json(line)
        if row["id"] in selected:
            require(row["id"] not in rows, "Duplicate assigned main source ID")
            rows[row["id"]] = row
    groups, flow = {}, []
    for number in manifest["sample"]["group_ids"]:
        try:
            require(number in rows, "Assigned ID absent from the pinned main data source")
            groups[number] = prepare_group(rows[number], catalog, config)
            status = {"group_id": number, "status": "eligible_before_model_outputs", "replacement": False}
        except ValueError as error:
            status = {"group_id": number, "status": "ineligible_before_model_outputs", "replacement": False,
                      "reason": str(error)}
        flow.append(status)
    return groups, {"assigned_ids": manifest["sample"]["group_ids"], "groups": flow, "model_outputs_used": False}


def verify_annotations(review, manifest, groups, prefixes):
    """Verify supplied human attestations, never manufacture human gold."""
    receipt = read_json(Path(review) / "human-source-annotations.json")
    require(receipt["schema"] == "human_main_source_annotations_v1" and receipt["role"] == "user" and
            receipt["message_verified_from_app"] is True and receipt["source_only_before_memory"] is True,
            "Human source-only mapping and formation review are required before memory")
    require(receipt["manifest_sha256"] == fingerprint(manifest) and
            receipt["registry_sha256"] == registry_digest(groups), "Human annotation belongs to another main registry")
    require(all(HUMAN_ID.fullmatch(receipt[key]) for key in ("human_message_id", "human_thread_id")) and
            SHA.fullmatch(receipt["verification_evidence_sha256"]), "Actual human annotation evidence is required")
    require(set(receipt["groups"]) == {str(number) for number in groups}, "Incomplete main annotation matrix")
    for number, group in groups.items():
        row = receipt["groups"][str(number)]
        source = prefixes / f"prefix/group-{number}/source.json"
        require(row["source_file_sha256"] == file_hash(source), "Human label refers to another actual source")
        require(row["Q_mapping_sha256"] == fingerprint([task["memory"] for task in group["tasks"][2:]]),
                "Human Q mapping differs from the frozen typed source mapping")
        require(type(row["harmful_future_agreement_present"]) is bool and row["formation_review_complete"] is True,
                "Human formation label is missing; formation cannot select which branches run")
        reference = Path(review) / row["reference_file"]
        require(reference.resolve().is_relative_to(Path(review).resolve()) and
                file_hash(reference) == row["reference_sha256"], "Source-first reference changed")
        binding = read_json(reference)
        require(binding["source_file_sha256"] == file_hash(source) and binding["memory_outputs_opened"] is False,
                "Source reference was not locked before memory outputs")
    return receipt


def continuation_group(prefix_job, packages, manifest, config, base, budget, work, *, group_registry, catalog,
                       generator_factory=None):
    """Six isolated arms, identical initial B/world, explicit missing results."""
    from .abe_cycle import actor_generator, ledger_accounting, load_prompts, namespace
    from .abe_evaluation import evaluate_opportunity
    from .abe_protocol import Protocol
    from .abe_runner import BranchRunner
    from .artifacts import append_event
    generator_factory = generator_factory or actor_generator
    number = group_registry["group_id"]
    require(read_json(prefix_job / "status.json")["status"] == "prefix_collected", "Incomplete main prefix")
    snapshot = read_json(prefix_job / "snapshot.json")
    protocol = Protocol(number, namespace(config["run_id"], number, "prefix"), catalog,
                        events=deepcopy(snapshot["events"]), commits=deepcopy(snapshot["commits"]))
    prefix = BranchRunner(protocol, load_prompts(config), config["limits"], None, base=None,
                          histories=deepcopy(snapshot["histories"]), private_goals=None,
                          accounting=lambda ids: ledger_accounting(budget, ids))
    arms = manifest["sample"]["arms"]
    order = sorted(arms, key=lambda arm: fingerprint([config["run_id"], number, base["generation"]["seed"], arm]))
    mapping = {arm: namespace(config["run_id"], number, "recipient/" + arm) for arm in arms}
    destination = work / f"continuations/group-{number}"
    require(not destination.exists(), "Main branches are immutable; no hidden retries")
    destination.mkdir(parents=True, mode=0o700)
    tasks = group_registry["tasks"][2:]
    assigned = [{"group_id": number, "arm": arm, "task_id": task["task_id"],
                 "opaque_branch_id": mapping[arm], "status": "assigned_not_run"} for arm in order for task in tasks]
    atomic_json(destination / "admin-assigned-matrix.json", {"order": order, "mapping": mapping, "opportunities": assigned})
    runners, starts, failure = {}, [], None
    try:
        for arm in order:
            job = destination / mapping[arm]
            job.mkdir(mode=0o700)
            if arm not in packages:
                atomic_json(job / "status.json", {"status": "not_run_missing_assigned_memory", "arm_admin_only": arm})
                continue
            runner = prefix.fork(mapping[arm], packages[arm]["content"],
                                 generator_factory(config, base, budget, job, "continuation_actors"),
                                 emit=lambda event, path=job / "events.jsonl": append_event(path, event),
                                 persist=lambda task_id, result, path=job: atomic_json(path / "results" / (task_id + ".json"), result))
            runners[arm] = runner
            starts.append({"branch_id": mapping[arm], "B": deepcopy(runner.histories["B"]),
                           "world_state_sha256": fingerprint(runner.protocol.commits), "private_goals": runner.private_goals})
        require(len({fingerprint({key: value for key, value in row.items() if key != "branch_id"}) for row in starts}) <= 1,
                "Main arms have different B/world starts")
        atomic_json(destination / "admin-start-states.json", starts)
        for arm in order:
            if arm in runners:
                for task in tasks:
                    runners[arm].run_task(task)
    except BaseException:
        failure = traceback.format_exc()
        raise
    finally:
        for row in assigned:
            arm = row["arm"]
            job = destination / mapping[arm]
            result_path = job / "results" / (row["task_id"] + ".json")
            if result_path.exists():
                row["status"] = read_json(result_path)["status"]
            else:
                task = next(task for task in tasks if task["task_id"] == row["task_id"])
                row["status"] = "not_run_after_phase_failure" if failure else "not_run_missing_assigned_memory"
                events = runners[arm].protocol.events if arm in runners else []
                atomic_json(result_path, {"task_id": row["task_id"], "status": row["status"], "reason": failure,
                            "model_calls": 0, "evaluation": evaluate_opportunity(task, catalog, events, ledger_complete=True)})
        for arm, runner in runners.items():
            job = destination / mapping[arm]
            atomic_json(job / "actual-requests.json", runner.requests)
            atomic_json(job / "snapshot.json", {"histories": runner.histories, "commits": runner.protocol.commits,
                        "events": runner.protocol.events})
            require(not runner.private_goals, "Direct private goal survived a main fork")
            require(all(event["branch_id"] == mapping[arm] for event in runner.protocol.events), "Cross-branch event leak")
            for request in runner.requests:
                require(all(origin["event_type"] != "private_goal_input" and
                            origin["message_sha256"] == fingerprint(request["payload"]["messages"][origin["message_index"]])
                            for origin in request["message_provenance"]), "Private-goal origin or wire-provenance mismatch")
        atomic_json(destination / "status.json", {"opportunities": assigned, "failure": failure,
                    "status": "stopped_with_assigned_statuses" if failure else "finished_with_explicit_statuses"})
    return destination


def preflight(manifest, config, base, assets, phase, *, code, review, root, work):
    """Executed only after approved_pipeline, before a model service exists."""
    from .abe_cycle import actor_generation, load_prompts, namespace
    from .abe_protocol import Protocol, action_schema, public_task
    from .abe_runner import BranchRunner
    from .artifacts import canonical
    from .catalog import load_accommodations
    from .memory_v4 import constrained_payload
    from transformers import AutoTokenizer
    catalog = load_accommodations("/database/accommodations/clean_accommodations_2022.csv", config["task_policy"]["catalog_csv_sha256"])
    groups, flow = prepare_registry(manifest, config, Path("/main-data") / config["main_data_source"]["filename"], catalog)
    atomic_json(work / "eligibility-flow.json", flow)
    require(set(groups) == set(manifest["sample"]["group_ids"]), "Assigned main group ineligible; preserve flow, never replace")
    probes = {}
    if phase == "prepare":
        atomic_json(work / "private-task-registry.json", {"groups": groups})
        atomic_json(work / "assigned-future-opportunities.json", assigned_opportunities(manifest, groups))
    else:
        prepared = read_json(root / "cpu-prepare/private-task-registry.json")["groups"]
        prepare_receipt = read_json(root / "cpu-prepare/preflight.json")
        require(prepare_receipt["manifest_sha256"] == fingerprint(manifest) and
                prepare_receipt["phase"] == "prepare" and prepare_receipt["status"] == "passed_no_model_calls",
                "Preparation belongs to another main manifest")
        require(registry_digest(groups) == registry_digest(prepared) == prepare_receipt["registry_sha256"],
                "Main registry changed after preparation")
        if phase == "prefixes":
            for number, group in groups.items():
                protocol = Protocol(number, namespace(config["run_id"], number, "prefix"), catalog)
                private = {role: (code / config["protocol"]["private_" + role + "_prompt"]).read_text() for role in ("A", "B")} if manifest["sample"]["prefix_condition_by_group"][str(number)] == "private_goal_induction" else {}
                runner = BranchRunner(protocol, load_prompts(config), config["limits"], None, base=group["base_person"], private_goals=private)
                task = group["tasks"][0]
                protocol.issue(task)
                for role in ("A", "B"):
                    runner.add(role, "user", "issued_task", canonical({"current_public_task": public_task(task)}).decode(), "user")
                schema = action_schema("A", tools_allowed=True)
                body = constrained_payload("qwen-native-dev", actor_generation(base), runner.messages("A", schema), schema, config["limits"]["actor_max_output_tokens"])
                probes[str(number)] = {"request": body}
            atomic_json(work / "actor-wire-probes.json", probes)
            atomic_json(work / "private-task-registry.json", {"groups": groups})
        else:
            bound = {number: read_json(root / f"prefixes/prefix/group-{number}/private-registry-bound.json") for number in groups}
            verify_annotations(review, manifest, bound, root / "prefixes")
            from .readiness_cycle import payload
            for number in groups:
                source = read_json(root / f"prefixes/prefix/group-{number}/source.json")
                for stage in ("S", "C"):
                    probes[f"group-{number}/{stage}"] = payload(stage, source, [], config, assets)
            atomic_json(work / "wire-probes.json", probes)
    if probes:
        tokenizer = AutoTokenizer.from_pretrained("/model", local_files_only=True)
        for probe in probes.values():
            body = probe.get("request", probe)
            count = len(tokenizer.apply_chat_template(body["messages"], tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False))
            require(count + body["max_tokens"] <= config["limits"]["context"], "Main fixed wire exceeds full context")
    receipt = {"schema": "main_cpu_preflight_v1", "phase": phase, "status": "passed_no_model_calls",
               "manifest_sha256": fingerprint(manifest), "registry_sha256": registry_digest(groups), "new_model_calls": 0,
               "code_commit": manifest["pipeline"]["code_commit"],
               "review_files_sha256": {name: file_hash(review / name) for name in
                                       ("main-manifest.json", "human-main-approval.json", "main-readiness.json")},
               "files": {path.name: file_hash(path) for path in work.glob("*.json")}}
    if phase != "prepare":
        receipt["prepared_files_sha256"] = {name: file_hash(root / "cpu-prepare" / name) for name in
                                           ("private-task-registry.json", "assigned-future-opportunities.json", "preflight.json")}
    if phase == "continuations":
        receipt["review_files_sha256"]["human-source-annotations.json"] = file_hash(review / "human-source-annotations.json")
    atomic_json(work / "preflight.json", receipt)


def run_phase(manifest, config, base, assets, budget, *, phase, review, root, work):
    from .abe_cycle import prefix_group
    from .catalog import load_accommodations
    catalog = load_accommodations("/database/accommodations/clean_accommodations_2022.csv", config["task_policy"]["catalog_csv_sha256"])
    groups = {int(key): value for key, value in read_json(root / "cpu-prepare/private-task-registry.json")["groups"].items()}
    assigned = read_json(root / "cpu-prepare/assigned-future-opportunities.json")
    require(assigned == assigned_opportunities(manifest, groups), "Frozen main denominator changed")
    atomic_json(work / "assigned-future-opportunities.json", assigned)
    failure = None
    try:
        if phase == "prefixes":
            for number in manifest["sample"]["group_ids"]:
                prefix_group(groups[number], catalog, config, base, budget, work,
                             induced=manifest["sample"]["prefix_condition_by_group"][str(number)] == "private_goal_induction",
                             allocation="prefix_actors")
        else:
            groups = {number: read_json(root / f"prefixes/prefix/group-{number}/private-registry-bound.json") for number in groups}
            verify_annotations(review, manifest, groups, root / "prefixes")
            from .readiness_cycle import memory_job
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained("/model", local_files_only=True)
            for number in manifest["sample"]["group_ids"]:
                prefix = root / f"prefixes/prefix/group-{number}"
                memory = work / f"memory/group-{number}"
                memory_job(read_json(prefix / "source.json"), memory, f"continuations/group-{number}", config, assets, budget,
                           lambda text: len(tokenizer.encode(text, add_special_tokens=False)), allocation="memory_builders")
                packages = {path.stem: read_json(path) for path in (memory / "automatic-withheld/packages").glob("*.json")}
                continuation_group(prefix, packages, manifest, config, base, budget, work, group_registry=groups[number], catalog=catalog)
    except BaseException:
        failure = traceback.format_exc()
        raise
    finally:
        if phase == "continuations":
            from .abe_cycle import namespace
            from .abe_evaluation import evaluate_opportunity
            report = []
            for item in assigned:
                number, arm = item["group_id"], item["arm"]
                task = next(task for task in groups[number]["tasks"] if task["task_id"] == item["task_id"])
                path = work / f"continuations/group-{number}" / namespace(config["run_id"], number, "recipient/" + arm) / "results" / (task["task_id"] + ".json")
                if path.exists():
                    try:
                        result = read_json(path)
                    except ValueError:
                        result = {"status": "measurement_corruption", "evaluation": {"H": None, "U": None, "Q": None}}
                else:
                    result = {"status": "not_run_after_phase_failure", "reason": failure, "model_calls": 0,
                              "evaluation": evaluate_opportunity(task, catalog, [], ledger_complete=True)}
                    atomic_json(path, result)
                report.append({**item, "result": result})
            atomic_json(work / "all-assigned-results.json", {"assigned": len(assigned), "observed": report})
        atomic_json(work / "phase-status.json", {"phase": phase, "status": "failed" if failure else "finished_with_explicit_statuses",
                    "failure": failure, "global_budget": budget.state(), "main_started": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", type=Path, help="Manifest only; no native data, model or network access")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--client", action="store_true")
    args = parser.parse_args()
    if args.validate:
        print(fingerprint(load_manifest(args.validate)))
        return
    os.umask(0o077)
    require(os.getuid() != 0 and Path("/.dockerenv").exists(), "Non-root Docker required")
    phase = os.environ["MAIN_PHASE"]
    require(phase in {"prepare", "prefixes", "continuations"}, "Unknown main phase")
    code, review, root, work = map(Path, ("/code", "/review", "/cycle", "/work"))
    manifest, config, base, assets, authority = approved_pipeline(review, code)
    if args.preflight:
        return preflight(manifest, config, base, assets, phase, code=code, review=review, root=root, work=work)
    require(phase != "prepare", "Preparation is CPU-only")
    pre = read_json(Path("/preflight/preflight.json"))
    require(pre["schema"] == "main_cpu_preflight_v1" and pre["phase"] == phase and
            pre["manifest_sha256"] == fingerprint(manifest), "Matching main CPU preflight required")
    require(registry_digest(read_json(root / "cpu-prepare/private-task-registry.json")["groups"]) == pre["registry_sha256"],
            "Prepared main registry differs from the CPU preflight")
    for name, digest in pre["review_files_sha256"].items():
        require(Path(name).name == name and file_hash(review / name) == digest, "Main approval packet changed after CPU review")
    for name, digest in pre["prepared_files_sha256"].items():
        require(Path(name).name == name and file_hash(root / "cpu-prepare" / name) == digest,
                "Prepared main artifact changed after CPU review")
    for name, digest in pre["files"].items():
        path = Path("/preflight") / name
        require(path.resolve().is_relative_to(Path("/preflight")) and file_hash(path) == digest, "Main CPU artifact changed")
    master = root / "main-manifest.json"
    if master.exists():
        require(read_json(master) == manifest, "Main manifest and budgets cannot be reset")
    else:
        atomic_json(master, manifest)
    if phase == "continuations":
        groups = {number: read_json(root / f"prefixes/prefix/group-{number}/private-registry-bound.json") for number in manifest["sample"]["group_ids"]}
        verify_annotations(review, manifest, groups, root / "prefixes")
    if args.client:
        import httpx
        from .cycle_budget import CycleBudget
        with httpx.Client(trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as http:
            return run_phase(manifest, config, base, assets, CycleBudget(root, config, http, phase_work=work),
                             phase=phase, review=review, root=root, work=work)
    from . import smoke
    atomic_json(work / "phase-manifest.json", {"phase": phase, "manifest_sha256": fingerprint(manifest),
                "authority": authority, "CPU_preflight_sha256": file_hash("/preflight/preflight.json"), "main_started": True})
    smoke.supervise(base, float(os.environ["PACKAGE_DEADLINE_UNIX"]), client_module="collusion_handoff.main_cycle",
                    alias="qwen-native-dev", scope="approved_main_only", server_extra_args=("--structured-outputs-config.backend", "xgrammar"))


if __name__ == "__main__":
    main()
