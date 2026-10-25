"""Freeze the prospective canonical-frame permutation without choosing main N."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("metadata", "science", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("metadata-sha256", "science-sha256"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    assert os.getuid() != 0 and Path("/.dockerenv").exists()
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == "" and not list(Path("/dev").glob("nvidia[0-9]*"))
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
    assert not args.output.exists()
    assert sha256(args.metadata.read_bytes()).hexdigest() == args.metadata_sha256
    assert sha256(args.science.read_bytes()).hexdigest() == args.science_sha256
    metadata, science = (json.loads(path.read_text()) for path in (args.metadata, args.science))
    assert metadata["science_freeze_sha256"] == args.science_sha256
    assert metadata["schema"] == "main_candidate_frame_metadata_receipt_v1"
    assert metadata["main_sample_chosen"] is False and metadata["model_outputs_used"] is False
    assert metadata["source_unit_policy"] == "exact_base_query_and_provided_base_plan_first_in_frame"
    ids = metadata["eligible_unique_canonical_ids_in_frame_order"]
    assert ids and ids == sorted(set(ids)) and len(ids) == metadata["eligible_unique_canonical_count"]
    assert ids == [row["group_id"] for row in metadata["flow"]["groups"] if row["status"] == "eligible_before_model_outputs"]
    assert metadata["flow"]["assigned_ids"] == science["sample"]["ordered_candidate_group_ids"]
    assert not set(ids) & set(science["sample"]["dev_exposure_group_ids"])
    assert metadata["source_sha256"] == science["sample"]["pinned_source_sha256"]
    seed = science["sample"]["randomization"]["seed"]
    assert seed == 20261025 and science["sample"]["randomization"]["replace"] is False
    permutation = np.random.Generator(np.random.PCG64(seed)).permutation(ids).tolist()
    assert sorted(permutation) == ids
    result = {"schema": "prospective_main_canonical_frame_permutation_v1",
              "candidate_metadata_sha256": args.metadata_sha256, "science_plan_sha256": args.science_sha256,
              "candidate_frame_file_sha256": metadata["frame_file_sha256"],
              "source_unit_policy": metadata["source_unit_policy"], "source_sha256": metadata["source_sha256"],
              "canonical_eligible_ids": ids, "full_permutation": permutation,
              "rng": "numpy.Generator(numpy.PCG64)", "numpy_version": np.__version__, "seed": seed,
              "N": None, "main_sample_chosen": False, "new_model_calls": 0,
              "selection_rule": "Use the first N only after the predeclared DEV resource calculation; no replacement",
              "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"eligible_units": len(ids), "N": None,
                      "permutation_file_sha256": sha256(args.output.read_bytes()).hexdigest(), "new_model_calls": 0}))


if __name__ == "__main__":
    main()
