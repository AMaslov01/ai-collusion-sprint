"""Inspect the frozen DEV screening failure without changing or replacing tasks."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

from collusion_handoff.abe_tasks import query_memories
from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.catalog import load_accommodations
from collusion_handoff.native_data import _extract_name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.getuid() == 0 or not Path("/.dockerenv").exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise RuntimeError("Non-root CPU-only Docker required")
    if args.output.exists():
        raise RuntimeError("Preserve prior diagnostics")
    freeze_file = Path("/review/final-DEV-validation-freeze.json")
    freeze, receipt = read_json(freeze_file), read_json(Path("/review/final-freeze-receipt.json"))
    digest = sha256(freeze_file.read_bytes()).hexdigest()
    if (receipt.get("manifest_sha256") != digest or receipt.get("status") != "reviewed_for_DEV_validation"
            or receipt.get("CPU_preflight_source_access_allowed") is not True
            or receipt.get("code_commit") != freeze["code_commit"]
            or freeze["main_allowed"] is not False or freeze["scope"]["groups"] != [6, 7]):
        raise RuntimeError("No matching source-access freeze receipt")
    config = read_json(Path("/code/configs/readiness-cycle-v5.json"))
    base = read_json(Path("/code") / config["base_runtime_config"])
    source = Path("/tasks/data.jsonl").read_bytes()
    if sha256(source).hexdigest() != base["data"]["file_hashes"]["sha256"]:
        raise RuntimeError("Pinned source changed")
    catalog = load_accommodations("/database/accommodations/clean_accommodations_2022.csv", config["task_policy"]["catalog_csv_sha256"])
    cities = sorted({item["city"] for item in catalog.values()})
    result = {}
    for line in source.decode().splitlines():
        row = json.loads(line)
        if row["id"] not in freeze["scope"]["groups"]:
            continue
        plans = row["base_person"]["daily_plans"]
        first = plans[0]["accommodation"]
        suffix_matches = [city for city in cities if first.endswith(", " + city)]
        try:
            parsed = query_memories(row["questions"][0], _extract_name(row["questions"][0]))
            q = {"status": "independent_Q_parser_accepts", "bounded_clause_count": len(parsed)}
        except Exception as error:
            q = {"status": "independent_Q_parser_rejects", "type": type(error).__name__, "message": str(error)}
        result[str(row["id"])] = {"base_person_name": row["base_person"]["name"],
            "base_daily_plan_destination_fields_exact": [
                {key: value for key, value in plan.items() if key in {"day", "current_city", "transportation", "accommodation"}}
                for plan in plans], "first_accommodation_exact": first,
            "frozen_first_accommodation_suffix_matches": suffix_matches,
            "diagnostic_all_day_accommodation_suffix_matches": [
                {"day_index": index, "accommodation": plan["accommodation"],
                 "matches": [city for city in cities if plan["accommodation"].endswith(", " + city)]}
                for index, plan in enumerate(plans, 1)],
            "base_query_exact": row["base_person"]["query"],
            "independent_first_prefix_query_parser_status_only": q,
            "frozen_screening_passed": len(suffix_matches) == 1}
    if set(result) != {"6", "7"}:
        raise RuntimeError("Assigned source rows unavailable")
    atomic_json(args.output, {"status": "source_only_diagnostic_no_selection_change", "groups": result, "canonical_city_names": cities,
        "source_file_sha256": sha256(source).hexdigest(), "freeze_sha256": digest,
        "new_model_calls": 0, "accepted_registry_created": False, "replacements": [],
        "future_traveler_queries_opened": False, "expected_answers_or_query_text_exported": False,
        "interpretation": "Other-day destination fields are diagnostic observations, not fallback rules or a replacement preflight pass."})


if __name__ == "__main__":
    main()
