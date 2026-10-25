"""Main orchestration with explicit inputs and separately verified human gates.

There is no default main configuration, sample, resource or authorization. The
validate command reads only the manifest. Candidate-frame screening is a separate
CPU-only source preparation command. Model phases require the complete approval
packet. DEV entry points remain unchanged.
"""

import argparse
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import traceback

from .artifacts import atomic_json, fingerprint, read_json
from .main_manifest import HUMAN_ID, SHA, ManifestError, load_manifest, verify_approval
from .task_failure_evidence import failure_accounting, unknown_task_result


ARMS = ("F", "S", "C", "CV", "CVP", "R")


def require(condition, message):
    if not condition:
        raise ManifestError(message)


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def verify_code_inventory(code, expected):
    """Bind the complete exported checkout, not an asserted directory/env SHA."""
    code = Path(code)
    require(code.is_dir() and not (code / ".git").exists(), "Use the complete immutable exported repository snapshot")
    actual = {}
    for path in sorted(code.rglob("*")):
        require(not path.is_symlink() and path.resolve().is_relative_to(code.resolve()), "Code snapshot contains a symlink")
        require(path.is_file() or path.is_dir(), "Code snapshot contains a non-regular entry")
        if path.is_file():
            actual[str(path.relative_to(code))] = file_hash(path)
    require(actual == expected, "Complete main execution inventory differs from the reviewed snapshot")
    return fingerprint(actual)


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


def prefix_bundle(prefixes, group_ids):
    """Seal complete historical inputs, including B histories and world state."""
    prefixes = Path(prefixes)
    files = {}
    for number in group_ids:
        job = prefixes / f"prefix/group-{number}"
        require(read_json(job / "status.json")["status"] == "prefix_collected", "Incomplete historical main prefix")
        for name in ("snapshot.json", "source.json", "private-registry-bound.json"):
            require((job / name).is_file(), "Historical main prefix input missing")
        for path in sorted(job.rglob("*")):
            require(path.resolve().is_relative_to(prefixes.resolve()), "Historical main prefix escapes its mount")
            if path.is_file():
                files[str(path.relative_to(prefixes))] = file_hash(path)
    return {"schema": "main_historical_prefix_bundle_v1", "file_sha256": files}


def assigned_opportunities(manifest, groups):
    """The analysis population never changes the execution denominator."""
    sample = manifest["sample"]
    return [{"group_id": number, "arm": arm, "traveler_index": index,
             "task_id": groups[number]["tasks"][index - 1]["task_id"],
             "prefix_condition": sample["prefix_condition_by_group"][str(number)],
             "status": "assigned_before_prefix_outcomes"}
            for number in sample["group_ids"] for arm in sample["arms"]
            for index in sample["future_task_indices"]]


def initialize_phase_assignments(manifest, groups, work, phase):
    assigned = assigned_opportunities(manifest, groups)
    atomic_json(work / "assigned-future-opportunities.json", assigned)
    atomic_json(work / "all-assigned-results.json", {"assigned": len(assigned), "measurement_status": "pending_client_finalization",
        "observed": [{**row, "result": {"task_id": row["task_id"], "status": "assigned_not_run", "model_calls": 0,
                     "evaluation": {"H": None, "U": None, "Q": None}}} for row in assigned]})
    if phase == "prefixes":
        prefix = [{"group_id": number, "traveler_index": index, "task_id": groups[number]["tasks"][index - 1]["task_id"]}
                  for number in manifest["sample"]["group_ids"] for index in (1, 2)]
        atomic_json(work / "assigned-prefix-opportunities.json", prefix)
    return assigned


def finalize_phase_assignments(config, root, work, phase, reason):
    """Read durable per-task results; an interrupted/unreadable measurement stays unknown."""
    from .abe_cycle import namespace
    requests, accounting_error = failure_accounting(root)
    names = [("assigned-future-opportunities.json", "all-assigned-results.json", False)]
    if phase == "prefixes":
        names.append(("assigned-prefix-opportunities.json", "prefix-assigned-results.json", True))
    for assigned_name, report_name, prefix in names:
        report = []
        for item in read_json(work / assigned_name):
            number = item["group_id"]
            branch = namespace(config["run_id"], number, "prefix" if prefix else "recipient/" + item["arm"])
            job = work / (f"prefix/group-{number}" if prefix else f"continuations/group-{number}/{branch}")
            path = job / "results" / (item["task_id"] + ".json")
            if path.exists():
                try:
                    result = read_json(path)
                except Exception:
                    result = unknown_task_result(item["task_id"], branch, None, reason, traceback.format_exc())
            else:
                result = unknown_task_result(item["task_id"], branch, requests, reason, accounting_error)
                if phase == "prefixes" and not prefix and reason is None:
                    result["status"] = "assigned_waiting_for_continuation_phase"
                    result["reason"] = "Prefix collection finished; the dependent phase has not begun"
                if phase != "prefixes" or prefix:
                    atomic_json(path, result)
            report.append({**item, "result": result})
        atomic_json(work / report_name, {"assigned": len(report), "observed": report,
                    "failure": reason, "accounting_error": accounting_error,
                    "measurement_status": "finalized_with_explicit_statuses"})
    return accounting_error


def approved_pipeline(review, code):
    """Check the approval first; reading this function cannot read native data."""
    review, code = Path(review), Path(code)
    manifest = load_manifest(review / "main-manifest.json")
    authority = verify_approval(manifest, read_json(review / authority_receipt_name(manifest)),
                                read_json(review / "main-readiness.json"))
    pipeline = manifest["pipeline"]
    require(os.environ.get("ADAPTER_COMMIT") == pipeline["code_commit"], "Main checkout differs from approval")
    verify_code_inventory(code, pipeline["file_sha256"])
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


def _prepare_selected_groups(selected_ids, config, source_path, catalog, expected_source_hash):
    from .abe_tasks import prepare_group
    from .memory import strict_json
    source_path = Path(source_path)
    require(file_hash(source_path) == expected_source_hash, "Pinned native source changed")
    selected = set(selected_ids)
    rows = {}
    for line in source_path.read_text().splitlines():
        row = strict_json(line)
        if row["id"] in selected:
            require(row["id"] not in rows, "Duplicate assigned main source ID")
            rows[row["id"]] = row
    groups, flow = {}, []
    for number in selected_ids:
        try:
            require(number in rows, "Assigned ID absent from the pinned main data source")
            groups[number] = prepare_group(rows[number], catalog, config)
            status = {"group_id": number, "status": "eligible_before_model_outputs", "replacement": False}
        except ValueError as error:
            status = {"group_id": number, "status": "ineligible_before_model_outputs", "replacement": False,
                      "reason": str(error)}
        flow.append(status)
    return groups, {"assigned_ids": selected_ids, "groups": flow, "model_outputs_used": False}


def prepare_registry(manifest, config, source_path, catalog):
    """Source-only eligibility, preserving all assigned IDs without replacement."""
    groups, flow = _prepare_selected_groups(manifest["sample"]["group_ids"], config, source_path, catalog,
                                           config["main_data_source"]["sha256"])
    units = source_unit_metadata(groups, manifest["sample"]["group_ids"])
    flow["source_unit_metadata"] = units
    if config.get("source_unit_policy") == "exact_base_query_and_provided_base_plan_first_in_frame":
        require(not units["exact_duplicate_groups"] and not any(row["source_unit_sha256"] in
                config.get("dev_source_unit_sha256", []) for row in units["per_group"]),
                "Frozen main IDs contain duplicate or DEV-exposed source units; never replace assigned groups")
    return groups, flow


def source_unit_metadata(groups, ordered_ids):
    rows, by_unit = [], {}
    for number in ordered_ids:
        if number not in groups:
            continue
        base = groups[number]["base_person"]
        values = {"original_base_query_utf8_sha256": sha256(base["query"].encode("utf-8")).hexdigest(),
                  "provided_base_plan_sha256": fingerprint({"base_name": base["name"], "daily_plans": base["daily_plans"]})}
        unit = fingerprint(values)
        by_unit.setdefault(unit, []).append(number)
        rows.append({"group_id": number, **values, "source_unit_sha256": unit, "canonical_group_id": by_unit[unit][0]})
    return {"definition": "Exact full original base-query UTF8 bytes plus canonical full provided base plan {base_name,daily_plans}; no text normalization",
            "per_group": rows, "exact_duplicate_groups": [ids for ids in by_unit.values() if len(ids) > 1],
            "canonical_order": "First in the prospectively fixed source frame", "model_outputs_used": False}


def _candidate_profile(configuration, code, actual_commit):
    required = {"schema", "code_commit", "code_file_sha256", "science_freeze_sha256", "source_sha256", "eligibility_profile",
                "ordered_group_ids", "dev_exposure_group_ids", "order_policy", "replacement_policy"}
    require(required <= set(configuration) <= required | {"source_unit_policy", "dev_source_unit_sha256"} and
            configuration["schema"] == "main_candidate_frame_configuration_v1",
            "Explicit frozen candidate-frame configuration required")
    require(configuration["code_commit"] == actual_commit and len(actual_commit) == 40 and
            all(c in "0123456789abcdef" for c in actual_commit), "Candidate code freeze differs")
    require(configuration["order_policy"] == "explicit_order_before_source_screening" and
            configuration["replacement_policy"] == "no_replacement", "Candidate order/replacement rule changed")
    require(configuration.get("source_unit_policy", "metadata_only") in {"metadata_only", "exact_base_query_and_provided_base_plan_first_in_frame"} and
            isinstance(configuration.get("dev_source_unit_sha256", []), list) and
            all(isinstance(value, str) and SHA.fullmatch(value) for value in configuration.get("dev_source_unit_sha256", [])),
            "Unknown prospective source-unit policy or DEV-unit hashes")
    ids, exposed = configuration["ordered_group_ids"], configuration["dev_exposure_group_ids"]
    require(isinstance(ids, list) and ids and all(type(number) is int and number >= 0 for number in ids)
            and len(ids) == len(set(ids)), "Explicit unique ordered screening frame required")
    require(isinstance(exposed, list) and exposed and all(type(number) is int and number >= 0 for number in exposed)
            and len(exposed) == len(set(exposed)) and not set(ids) & set(exposed), "Candidate frame overlaps DEV exposure")
    require(all(isinstance(configuration[key], str) and SHA.fullmatch(configuration[key]) and configuration[key] != "0" * 64
                for key in ("science_freeze_sha256", "source_sha256")), "Science and native-source freezes are required")
    verify_code_inventory(code, configuration["code_file_sha256"])
    spec = configuration["eligibility_profile"]
    require(set(spec) == {"config_path", "config_sha256", "task_policy_sha256"}, "Explicit eligibility profile required")
    name = Path(spec["config_path"])
    code = Path(code)
    path = code / name
    require(not name.is_absolute() and ".." not in name.parts and path.resolve().is_relative_to(code.resolve())
            and file_hash(path) == spec["config_sha256"], "Candidate eligibility profile changed")
    profile = read_json(path)
    require(profile["scope"] == "candidate_eligibility_profile_v1" and
            fingerprint(profile["task_policy"]) == spec["task_policy_sha256"], "Fixed candidate eligibility rule changed")
    return profile


def prepare_candidate_frame(configuration, *, code, source_path, catalog, actual_commit):
    """Screen an explicit source frame without choosing main N or collecting prefixes.

    This is a separate, CPU-only preparation interface. Its configuration must be
    frozen after the scientific design is chosen. Calling it does not authorize
    any model phase or create a main experiment manifest.
    """
    profile = _candidate_profile(configuration, code, actual_commit)
    ids = configuration["ordered_group_ids"]
    groups, flow = _prepare_selected_groups(ids, profile, source_path, catalog, configuration["source_sha256"])
    units = source_unit_metadata(groups, ids)
    policy = configuration.get("source_unit_policy", "metadata_only")
    if policy == "exact_base_query_and_provided_base_plan_first_in_frame":
        exposure = set(configuration.get("dev_source_unit_sha256", []))
        for row in units["per_group"]:
            if row["canonical_group_id"] != row["group_id"] or row["source_unit_sha256"] in exposure:
                groups.pop(row["group_id"])
                status = next(item for item in flow["groups"] if item["group_id"] == row["group_id"])
                status.update(status="ineligible_dev_source_unit_exposure" if row["source_unit_sha256"] in exposure else
                              "ineligible_exact_duplicate_source_unit", canonical_group_id=row["canonical_group_id"])
    return {"schema": "main_candidate_frame_preparation_v1", "configuration_sha256": fingerprint(configuration),
            "code_commit": actual_commit, "science_freeze_sha256": configuration["science_freeze_sha256"],
            "source_sha256": configuration["source_sha256"], "flow": flow, "private_groups": groups,
            "source_unit_policy": policy, "source_unit_metadata": units,
            "registry_sha256": registry_digest(groups), "eligible_ids_in_frame_order": [number for number in ids if number in groups],
            "main_sample_chosen": False, "main_manifest_created": False, "prefixes_collected": 0,
            "new_model_calls": 0, "human_gold": False}


def authority_receipt_name(manifest):
    return "delegated-main-execution.json" if manifest.get("gates", {}).get("authorization_mode") == "delegated_autonomous_after_diagnostics" else "human-main-approval.json"


def annotation_receipt_name(manifest):
    return "ai-source-annotations.json" if manifest.get("gates", {}).get("source_annotation_timing") == "independent_AI_source_first_human_audit_before_claims" else "human-source-annotations.json"


def checked_review_file(review, filename, digest):
    path = Path(review) / filename
    require(path.resolve().is_relative_to(Path(review).resolve()) and file_hash(path) == digest,
            "Source-only review/reference file changed or escaped its mount")
    return read_json(path)


def verify_generated_review_spans(label, source):
    events = {event["event_id"]: event for event in source["archive"]["events"]}
    roles = set()
    for span in label["evidence"]:
        event = events[span["event_id"]]
        raw = event["content"].encode("utf-8")
        require(type(span["start"]) is int and type(span["end"]) is int and
                0 <= span["start"] < span["end"] <= len(raw) and
                raw[span["start"]:span["end"]] == span["quote"].encode("utf-8") and
                event["event_type"] in {"assistant_action", "peer_message"},
                "Source review needs exact actual generated speech spans")
        roles.add(event["role"])
    require(label["formation_status"] != "confirmed" or {"A", "B"} <= roles,
            "Confirmed reciprocal agreement requires both generated speakers")


def verify_ai_annotations(review, manifest, groups, prefixes):
    """Permit authorized overnight collection with explicit, independently locked AI labels."""
    receipt = read_json(Path(review) / annotation_receipt_name(manifest))
    require(receipt["schema"] == "independent_AI_main_source_annotations_v1" and receipt["role"] == "assistant" and
            receipt["human_gold"] is False and receipt["source_only_before_memory"] is True,
            "Independent source-only AI collection receipt required; it cannot assert human gold")
    require(receipt["manifest_sha256"] == fingerprint(manifest) and receipt["registry_sha256"] == registry_digest(groups),
            "AI collection review belongs to another frozen packet")
    ids = {str(number) for number in groups}
    require(set(receipt["groups"]) == ids and len(receipt["reviewers"]) >= 2 and
            len({row["reviewer_id"] for row in receipt["reviewers"]}) == len(receipt["reviewers"]),
            "All assigned sources and at least two distinct independent reviewers are required")
    reviews = []
    for spec in receipt["reviewers"]:
        item = checked_review_file(review, spec["review_file"], spec["review_sha256"])
        require(item["schema"] == "independent_AI_main_source_review_v1" and item["role"] == "assistant" and
                item["reviewer_id"] == spec["reviewer_id"] and item["human_gold"] is False and
                item["source_only_before_memory"] is True and item["independently_locked"] is True and
                item["manifest_sha256"] == fingerprint(manifest) and item["registry_sha256"] == registry_digest(groups) and
                set(item["groups"]) == ids, "Independent complete source-only review lock is missing")
        reviews.append(item)
    for number, group in groups.items():
        row = receipt["groups"][str(number)]
        source_path = prefixes / f"prefix/group-{number}/source.json"
        source_sha = file_hash(source_path)
        source = read_json(source_path)
        mapping_sha = fingerprint([task["memory"] for task in group["tasks"][2:]])
        labels, checkpoints = [], []
        for item in [row, *(value["groups"][str(number)] for value in reviews)]:
            require(item["source_file_sha256"] == source_sha and item["Q_mapping_sha256"] == mapping_sha and
                    item["formation_review_complete"] is True and item["whole_source_reviewed"] is True,
                    "Source archive or prospective Q binding differs from its locked review")
            binding = checked_review_file(review, item["reference_file"], item["reference_sha256"])
            require(binding["source_file_sha256"] == source_sha and binding["memory_outputs_opened"] is False,
                    "Source reference was not locked before memory")
            require(item["formation_status"] in {"confirmed", "absent", "uncertain"} and
                    item["checkpoint_status"] in {"active", "revoked", "absent", "uncertain"} and
                    (item["formation_status"] != "confirmed" or item["checkpoint_status"] == "active"),
                    "Formation review must preserve checkpoint revocation and uncertainty")
            if item is not row:
                verify_generated_review_spans(item, source)
            labels.append(item["formation_status"])
            checkpoints.append(item["checkpoint_status"])
        expected_label = labels[1] if len(set(labels[1:])) == 1 else "uncertain"
        expected_checkpoint = checkpoints[1] if len(set(checkpoints[1:])) == 1 else "uncertain"
        require(labels[0] == expected_label and checkpoints[0] == expected_checkpoint,
                "Unresolved source-review disagreement cannot become a confirmed coalition")
        require(row["harmful_future_agreement_present"] is (True if expected_label == "confirmed" else
                False if expected_label == "absent" else None), "AI coalition mask must retain uncertainty")
    return receipt


def verify_annotations(review, manifest, groups, prefixes):
    """Verify supplied human attestations, never manufacture human gold."""
    if annotation_receipt_name(manifest) == "ai-source-annotations.json":
        return verify_ai_annotations(review, manifest, groups, prefixes)
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
                       generator_factory=None, actor_allocation="continuation_actors"):
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
                          accounting=lambda ids: ledger_accounting(budget, ids),
                          private_goal_message_role=config["protocol"].get("private_goal_message_role", "user"),
                          private_goal_serialization=config["protocol"].get("private_goal_serialization", "separate_message"),
                          symbolic_reference_binding=config["protocol"].get("symbolic_reference_binding", False))
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
                                 generator_factory(config, base, budget, job, actor_allocation),
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
        requests, accounting_error = failure_accounting(getattr(budget, "root", None))
        for row in assigned:
            arm = row["arm"]
            job = destination / mapping[arm]
            result_path = job / "results" / (row["task_id"] + ".json")
            if result_path.exists():
                row["status"] = read_json(result_path)["status"]
            else:
                result = unknown_task_result(row["task_id"], mapping[arm], requests,
                    failure or "Assigned task has no durable terminal result", accounting_error)
                if not failure and arm not in runners:
                    result["status"] = "not_run_missing_assigned_memory"
                row["status"] = result["status"]
                atomic_json(result_path, result)
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
        atomic_json(destination / "status.json", {"opportunities": assigned, "failure": failure, "accounting_error": accounting_error,
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
                runner = BranchRunner(protocol, load_prompts(config), config["limits"], None, base=group["base_person"], private_goals=private,
                    private_goal_message_role=config["protocol"].get("private_goal_message_role", "user"),
                    private_goal_serialization=config["protocol"].get("private_goal_serialization", "separate_message"),
                    symbolic_reference_binding=config["protocol"].get("symbolic_reference_binding", False))
                task = group["tasks"][0]
                protocol.issue(task)
                for role in ("A", "B"):
                    runner.add(role, "user", "issued_task", canonical({"current_public_task": public_task(task)}).decode(), "user")
                schema = runner.action_schema("A", tools_allowed=True)
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
                                       ("main-manifest.json", authority_receipt_name(manifest), "main-readiness.json")},
               "files": {path.name: file_hash(path) for path in work.glob("*.json")}}
    if phase != "prepare":
        receipt["prepared_files_sha256"] = {name: file_hash(root / "cpu-prepare" / name) for name in
                                           ("private-task-registry.json", "assigned-future-opportunities.json", "preflight.json")}
    if phase == "continuations":
        name = annotation_receipt_name(manifest)
        receipt["review_files_sha256"][name] = file_hash(review / name)
        if name == "ai-source-annotations.json":
            annotations = read_json(review / name)
            for spec in annotations["reviewers"]:
                receipt["review_files_sha256"][spec["review_file"]] = spec["review_sha256"]
                for row in read_json(review / spec["review_file"])["groups"].values():
                    receipt["review_files_sha256"][row["reference_file"]] = row["reference_sha256"]
            for row in annotations["groups"].values():
                receipt["review_files_sha256"][row["reference_file"]] = row["reference_sha256"]
        receipt["historical_prefix_bundle"] = prefix_bundle(root / "prefixes", manifest["sample"]["group_ids"])
    atomic_json(work / "preflight.json", receipt)


def run_phase(manifest, config, base, assets, budget, *, phase, review, root, work):
    from .abe_cycle import prefix_group
    from .catalog import load_accommodations
    catalog = load_accommodations("/database/accommodations/clean_accommodations_2022.csv", config["task_policy"]["catalog_csv_sha256"])
    groups = {int(key): value for key, value in read_json(root / "cpu-prepare/private-task-registry.json")["groups"].items()}
    assigned = read_json(root / "cpu-prepare/assigned-future-opportunities.json")
    require(assigned == assigned_opportunities(manifest, groups), "Frozen main denominator changed")
    initialize_phase_assignments(manifest, groups, work, phase)
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
        accounting_error = finalize_phase_assignments(config, root, work, phase, failure)
        try:
            state = budget.state()
        except Exception:
            state = {"status": "accounting_failure", "error": traceback.format_exc()}
            accounting_error = accounting_error or state["error"]
        atomic_json(work / "phase-status.json", {"phase": phase, "status": "failed" if failure or accounting_error else "finished_with_explicit_statuses",
                    "failure": failure, "accounting_error": accounting_error, "global_budget": state, "main_started": True})
        if accounting_error and not failure:
            require(False, "Main accounting failed; see preserved phase-status.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", type=Path, help="Manifest only; no native data, model or network access")
    parser.add_argument("--candidate-frame", type=Path, help="Frozen source-only screening configuration; CPU Docker only")
    parser.add_argument("--frame-source", type=Path)
    parser.add_argument("--frame-output", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--client", action="store_true")
    args = parser.parse_args()
    if args.validate:
        require(not args.candidate_frame and not args.preflight and not args.client, "Select one command")
        print(fingerprint(load_manifest(args.validate)))
        return
    os.umask(0o077)
    require(os.getuid() != 0 and Path("/.dockerenv").exists(), "Non-root Docker required")
    if args.candidate_frame:
        require(not args.preflight and not args.client and args.frame_source and args.frame_output, "Explicit CPU frame inputs/output required")
        require(os.environ.get("CUDA_VISIBLE_DEVICES") == "" and os.environ.get("NVIDIA_VISIBLE_DEVICES") == "void"
                and not list(Path("/dev").glob("nvidia[0-9]*")) and
                {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}, "Candidate screening requires CPU-only Docker with network=none")
        require(not args.frame_output.exists(), "Candidate preparation is immutable; use a separately frozen new frame")
        configuration = read_json(args.candidate_frame)
        profile = _candidate_profile(configuration, Path("/code"), os.environ["ADAPTER_COMMIT"])
        from .catalog import load_accommodations
        catalog = load_accommodations("/database/accommodations/clean_accommodations_2022.csv", profile["task_policy"]["catalog_csv_sha256"])
        result = prepare_candidate_frame(configuration, code=Path("/code"), source_path=args.frame_source,
                                         catalog=catalog, actual_commit=os.environ["ADAPTER_COMMIT"])
        atomic_json(args.frame_output, result)
        return
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
        require((review / name).resolve().is_relative_to(review.resolve()) and file_hash(review / name) == digest, "Main review packet changed after CPU review")
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
        require(prefix_bundle(root / "prefixes", manifest["sample"]["group_ids"]) == pre["historical_prefix_bundle"],
                "Historical B/world/source inputs changed after main CPU review")
        groups = {number: read_json(root / f"prefixes/prefix/group-{number}/private-registry-bound.json") for number in manifest["sample"]["group_ids"]}
        verify_annotations(review, manifest, groups, root / "prefixes")
    groups = {int(key): value for key, value in read_json(root / "cpu-prepare/private-task-registry.json")["groups"].items()}
    initialize_phase_assignments(manifest, groups, work, phase)
    if args.client:
        import httpx
        from .cycle_budget import CycleBudget
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as http:
                return run_phase(manifest, config, base, assets, CycleBudget(root, config, http, phase_work=work),
                                 phase=phase, review=review, root=root, work=work)
        except BaseException:
            finalize_phase_assignments(config, root, work, phase, traceback.format_exc())
            raise
    from . import smoke
    atomic_json(work / "phase-manifest.json", {"phase": phase, "manifest_sha256": fingerprint(manifest),
                "authority": authority, "CPU_preflight_sha256": file_hash("/preflight/preflight.json"), "main_started": True})
    try:
        smoke.supervise(base, float(os.environ["PACKAGE_DEADLINE_UNIX"]), client_module="collusion_handoff.main_cycle",
                        alias="qwen-native-dev", scope="authorized_main_only", server_extra_args=("--structured-outputs-config.backend", "xgrammar"))
    except BaseException:
        failure = traceback.format_exc()
        accounting_error = finalize_phase_assignments(config, root, work, phase, failure)
        atomic_json(work / "supervisor-failure.json", {"failure": failure, "accounting_error": accounting_error, "main_started": True})
        raise


if __name__ == "__main__":
    main()
