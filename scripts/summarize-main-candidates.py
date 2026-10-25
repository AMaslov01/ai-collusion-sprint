"""Export candidate-screening metadata without queries, plans or model outputs."""

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert os.getuid() != 0 and Path("/.dockerenv").exists()
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == "" and not list(Path("/dev").glob("nvidia[0-9]*"))
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
    assert not args.output.exists()
    frame = json.loads(args.frame.read_text())
    config = json.loads(args.configuration.read_text())
    assert frame["schema"] == "main_candidate_frame_preparation_v1" and frame["new_model_calls"] == 0
    assert frame["main_sample_chosen"] is False and frame["main_manifest_created"] is False
    assert frame["prefixes_collected"] == 0 and frame["flow"]["model_outputs_used"] is False
    assert frame["flow"]["assigned_ids"] == config["ordered_group_ids"]
    assert len(frame["flow"]["groups"]) == len(config["ordered_group_ids"])
    assert frame["source_unit_policy"] == "exact_base_query_and_provided_base_plan_first_in_frame"
    assert len(frame["eligible_ids_in_frame_order"]) == len(set(frame["eligible_ids_in_frame_order"]))
    report = {
        "schema": "main_candidate_frame_metadata_receipt_v1",
        "frame_file_sha256": sha256(args.frame.read_bytes()).hexdigest(),
        "configuration_file_sha256": sha256(args.configuration.read_bytes()).hexdigest(),
        "configuration_sha256": frame["configuration_sha256"],
        "code_commit": frame["code_commit"], "science_freeze_sha256": frame["science_freeze_sha256"],
        "source_sha256": frame["source_sha256"], "registry_sha256": frame["registry_sha256"],
        "flow": frame["flow"], "status_counts": dict(Counter(row["status"] for row in frame["flow"]["groups"])),
        "eligible_unique_canonical_ids_in_frame_order": frame["eligible_ids_in_frame_order"],
        "eligible_unique_canonical_count": len(frame["eligible_ids_in_frame_order"]),
        "source_unit_policy": frame["source_unit_policy"], "source_unit_metadata": frame["source_unit_metadata"],
        "new_model_calls": 0, "main_sample_chosen": False, "main_manifest_created": False,
        "model_outputs_used": False, "human_gold": False,
        "summarizer_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status_counts": report["status_counts"],
                      "eligible_unique_canonical_count": report["eligible_unique_canonical_count"],
                      "metadata_receipt_sha256": sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
