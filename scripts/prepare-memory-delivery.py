"""Lossless delivery packaging; keep the frozen v2 outputs and all four calls intact."""

from hashlib import sha256
import os
from pathlib import Path
import time

from transformers import AutoTokenizer

from collusion_handoff.artifacts import atomic_json, canonical, fingerprint, read_json
from collusion_handoff.memory import MODEL_EVENT_FIELDS, model_source_view, require


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    os.umask(0o077)
    require(os.getuid() != 0 and time.time() < 1792840380 and not Path("/control/STOP").exists(), "Non-root live approved window required")
    completed, work = Path("/completed"), Path("/work")
    parent = read_json(completed / "manifest.json")
    parent_report = read_json(completed / "report.json")
    parent_audit = read_json(Path("/v2audit/audit.json"))
    require(parent_report["status"] == "built_format_recovery_variant_needs_independent_human_annotation", "Incomplete parent packages")
    require(parent_audit["status"] == "passed_cache_only" and parent_audit["plan_sha256"] == parent["plan_sha256"], "Parent cache audit missing")
    require(parent_report["model_attempts"] == 4 and parent_report["new_model_attempts"] == 3, "Cumulative attempt accounting changed")
    source = read_json(completed / "source.json")
    require(fingerprint(source["archive"]) == source["archive_sha256"] == parent["plan"]["source_archive_sha256"], "Full source hash changed")
    view = model_source_view(source["archive"], list(MODEL_EVENT_FIELDS))
    require(view == read_json(completed / "model-view.json") and fingerprint(view) == parent["plan"]["model_source_view_sha256"], "Approved compact view changed")
    tokens = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    packages = work / "automatic-withheld/packages"
    packages.mkdir(parents=True, mode=0o700)
    references = {}
    for arm in ("R", "S", "C", "CV", "CVP"):
        original = completed / f"automatic-withheld/packages/{arm}.json"
        content = original.read_bytes()
        destination = packages / f"{arm}.json"
        with destination.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        require(destination.read_bytes() == content, "Existing package bytes changed")
        value = read_json(destination)
        require(len(tokens.encode(value["content"], add_special_tokens=False)) == value["content_tokens"] <= 2048, "Existing package token budget changed")
        references[arm] = {"file_sha256": digest(destination), "content_sha256": value["content_sha256"],
                           "content_tokens": value["content_tokens"], "identical_to_frozen_v2": True}
    archive_id, view_id = source["archive_sha256"], fingerprint(view)
    transmitted = {"archive_id": archive_id, "source_view_id": view_id, **view}
    full_content = canonical(transmitted).decode()
    count = len(tokens.encode(full_content, add_special_tokens=False))
    require(count < 28672, "Compact F alone exceeds the continuation input allowance")
    frozen_f = read_json(completed / "automatic-withheld/packages/F.json")
    full = {"arm": "F", "content": full_content, "content_sha256": sha256(full_content.encode()).hexdigest(),
            "content_tokens": count, "wrapped": "[PRIVATE ARCHIVE]\n" + full_content + "\n[/PRIVATE ARCHIVE]",
            "status": "built_lossless_compact_full_archive", "source_archive_sha256": archive_id,
            "model_source_view_sha256": view_id, "source_event_count": len(view["source_events"]),
            "technical_metadata_externalized": ["delivery_refs", "first_delivery"],
            "source_version_retained_once": True, "event_content_removed_or_rewritten": False,
            "frozen_v2_F_content_tokens": frozen_f["content_tokens"],
            "actor_continuation_full_request_validated": False}
    atomic_json(packages / "F.json", full)
    references["F"] = {"file_sha256": digest(packages / "F.json"), "content_sha256": full["content_sha256"],
                       "content_tokens": count, "identical_to_frozen_v2": False, "all_source_events_and_text_preserved": True}
    # Validate the saved delivery, not only the in-memory projection.
    saved = read_json(packages / "F.json")
    require(saved == full, "Delivery F write changed")
    saved_events = read_json(completed / "model-view.json")["source_events"]
    require(transmitted["source_events"] == saved_events, "Event identity/order/content/derived links changed")
    result = {"status": "passed_lossless_delivery_packaging", "scope": "native_group1_cp2_only", "version": "memory_delivery_v1",
              "adapter_commit": os.environ["ADAPTER_COMMIT"], "new_model_calls": 0, "cumulative_model_attempts": 4,
              "parent_v2_plan_sha256": parent["plan_sha256"], "parent_v2_report_sha256": digest(completed / "report.json"),
              "parent_v2_cache_audit_sha256": digest(Path("/v2audit/audit.json")),
              "parent_v2_cache_replayed_artifacts": parent_audit["replayed_artifacts"],
              "original_strict_S_failure_preserved": True, "original_oversized_F_preserved": True,
              "source_archive_sha256": archive_id, "model_source_view_sha256": view_id,
              "source_event_count": len(view["source_events"]), "packages": references,
              "authority": {"thread_id": "01a1688e-8fee-7140-b505-6f93e571c740",
                            "human_message_id": "01a16d6c-c218-7f30-b48a-aa9dd907486f",
                            "scope": "Routine engineering and lossless compact packing within approved example; no scientific or compute-budget change"},
              "human_completed_forms": 0, "human_metrics": "pending_independent_source_first_annotation",
              "future_actor_continuation": "Not run; the full rendered current request must still pass its context check",
              "groups2_12_started": False, "two_agent_dev_started": False, "main_started": False}
    atomic_json(work / "delivery-manifest.json", result)


if __name__ == "__main__":
    main()
