"""Offline main-manifest and approval checks; no native data or network access.

An approval receipt is an attestation recorded after reading the actual human
message in the app. This module checks its binding, not the app account itself;
agents must never manufacture that attestation from a proposed action.
"""

from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import Path, PurePosixPath
import re

from jsonschema import Draft202012Validator

from .artifacts import fingerprint, read_json
from .memory import strict_json


class ManifestError(ValueError):
    pass


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas/main-manifest.schema.json"
SHA = re.compile(r"[0-9a-f]{64}\Z")
HUMAN_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
PLACEHOLDER = re.compile(r"\b(?:UNSET|TBD|TODO|PLACEHOLDER)\b|<[^>]+>", re.I)


def _require(condition, message):
    if not condition:
        raise ManifestError(message)


def _utc(value):
    try:
        _require(isinstance(value, str) and value.endswith("Z"), "UTC timestamp must end in Z")
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as error:
        raise ManifestError("Invalid UTC timestamp") from error


def _placeholders(value):
    if isinstance(value, str):
        _require(not PLACEHOLDER.search(value), "Unresolved placeholder in manifest")
        _require(value not in {"0" * 40, "0" * 64}, "Unresolved zero hash")
    elif isinstance(value, float):
        _require(math.isfinite(value), "Non-finite numeric configuration")
    elif isinstance(value, dict):
        for key, item in value.items():
            _placeholders(key)
            _placeholders(item)
    elif isinstance(value, list):
        for item in value:
            _placeholders(item)


def _relative(path):
    value = PurePosixPath(path)
    _require(not value.is_absolute() and ".." not in value.parts and str(value) == path
             and path not in {"", "."}, "Pipeline paths must be normalized relative paths")


def manifest_digest(value):
    return fingerprint(value)


def validate_manifest(value):
    """Validate explicit choices without reading a dataset, config, model or GPU."""
    errors = sorted(Draft202012Validator(read_json(SCHEMA_PATH)).iter_errors(value),
                    key=lambda item: str(list(item.path)))
    _require(not errors, "Main manifest schema: " + (errors[0].message if errors else ""))
    _placeholders(value)
    sample = value["sample"]
    groups, exposed = set(sample["group_ids"]), set(sample["dev_exposure_group_ids"])
    _require(len(groups) == sample["assigned_group_count"], "Assigned group count differs from IDs")
    _require(not groups & exposed, "Main groups overlap the exposure denylist")
    _require(set(sample["prefix_condition_by_group"]) == {str(number) for number in groups},
             "Each assigned group needs exactly one explicit prefix condition")
    for contrast in [value["science"]["primary_contrast"], *value["science"]["secondary_contrasts"]]:
        _require(contrast["minuend"] != contrast["subtrahend"], "A contrast needs different arms")
    _require(value["science"]["primary_contrast"]["metric"] == "H", "Primary harm estimand must be explicit H")
    budget = value["budget"]
    _require(sum(budget["allocations"].values()) == budget["max_model_attempts"],
             "Allocation totals must equal the finite main attempt ceiling")
    _utc(budget["absolute_cutoff_utc"])
    hardware = value["hardware"]
    _require(len(hardware["gpu_uuids"]) == hardware["tensor_parallel_size"] * hardware["endpoint_count"],
             "GPU count does not match the serving topology")
    _require(len(hardware["gpu_names"]) == len(hardware["gpu_uuids"]), "Name every reserved GPU")
    _require(hardware["shm_GiB"] <= hardware["RAM_limit_GiB"], "Shared memory exceeds the RAM ceiling")
    pipeline = value["pipeline"]
    _relative(pipeline["config_path"])
    for path in pipeline["file_sha256"]:
        _relative(path)
    _require("src/collusion_handoff/abe_evaluation.py" in pipeline["file_sha256"],
             "Canonical evaluator hash is required")
    run_root = PurePosixPath(value["artifacts"]["run_root"])
    _require(run_root.is_absolute() and ".." not in run_root.parts and run_root.name == value["run_id"],
             "Use a dedicated absolute run directory named by run_id")
    return deepcopy(value)


def load_manifest(path):
    try:
        return validate_manifest(strict_json(Path(path).read_text()))
    except ValueError as error:
        raise ManifestError(str(error)) from error


def verify_approval(manifest, receipt, readiness_receipt):
    """Bind separately verified human approval to this complete review packet."""
    manifest = validate_manifest(manifest)
    expected_readiness = {"schema", "status", "technical_checks_passed", "main_started", "human_gold",
                          "development_report_sha256", "exposure_group_ids", "inference_code_commit",
                          "pipeline_config_sha256", "hardware_sha256", "measured_limitations"}
    _require(isinstance(readiness_receipt, dict) and set(readiness_receipt) == expected_readiness,
             "Complete main readiness receipt is required")
    ready = readiness_receipt
    _placeholders(ready)
    _require(ready["schema"] == "main_readiness_receipt_v1" and ready["status"] == "ready_for_human_review"
             and ready["technical_checks_passed"] is True and ready["main_started"] is False
             and ready["human_gold"] is False, "Readiness cannot authorize main or invent human gold")
    _require(isinstance(ready["development_report_sha256"], str) and SHA.fullmatch(ready["development_report_sha256"]),
             "Completed development report hash missing")
    _require(isinstance(ready["exposure_group_ids"], list) and
             all(type(item) is int and item >= 0 for item in ready["exposure_group_ids"]), "Invalid readiness exposure list")
    _require(set(ready["exposure_group_ids"]) <= set(manifest["sample"]["dev_exposure_group_ids"]),
             "Manifest omits development exposure IDs")
    _require(ready["inference_code_commit"] == manifest["pipeline"]["code_commit"]
             and ready["pipeline_config_sha256"] == manifest["pipeline"]["config_sha256"]
             and ready["hardware_sha256"] == fingerprint(manifest["hardware"]),
             "Readiness refers to another pipeline or hardware freeze")
    _require(isinstance(ready["measured_limitations"], list) and
             all(isinstance(item, str) and item.strip() for item in ready["measured_limitations"]),
             "Measured limitations must be explicit")
    readiness_hash = fingerprint(ready)
    _require(readiness_hash == manifest["gates"]["readiness_receipt_sha256"], "Readiness digest differs")
    required = {"schema", "role", "human_message_id", "human_thread_id", "approved_manifest_sha256",
                "approved_readiness_receipt_sha256", "approved_at_utc", "message_verified_from_app",
                "verification_evidence_sha256", "scope"}
    _require(isinstance(receipt, dict) and set(receipt) == required, "Actual human main approval receipt required")
    _placeholders(receipt)
    _require(receipt["schema"] == "human_main_approval_receipt_v1" and receipt["role"] == "user"
             and receipt["message_verified_from_app"] is True and receipt["scope"] == "main_prefixes_and_continuations",
             "DEV or assistant authorization cannot authorize main")
    _require(all(isinstance(receipt[key], str) and HUMAN_ID.fullmatch(receipt[key])
                 for key in ("human_message_id", "human_thread_id")), "Actual human app message/thread IDs required")
    _require(isinstance(receipt["verification_evidence_sha256"], str) and SHA.fullmatch(receipt["verification_evidence_sha256"]),
             "Hash of the actual human-message verification evidence is required")
    _require(receipt["approved_manifest_sha256"] == fingerprint(manifest)
             and receipt["approved_readiness_receipt_sha256"] == readiness_hash, "Approval is bound to another packet")
    approved_at = _utc(receipt["approved_at_utc"])
    _require(approved_at <= datetime.now(timezone.utc), "Approval timestamp is in the future")
    _require(approved_at < _utc(manifest["budget"]["absolute_cutoff_utc"]), "Approval postdates the run cutoff")
    return {"approved_manifest_sha256": fingerprint(manifest), "approved_readiness_receipt_sha256": readiness_hash,
            "human_message_id": receipt["human_message_id"], "human_thread_id": receipt["human_thread_id"],
            "verification_evidence_sha256": receipt["verification_evidence_sha256"]}
