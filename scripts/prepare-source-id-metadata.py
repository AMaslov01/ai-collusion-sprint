"""CPU-only source inventory: all IDs, and base-input hashes for exposed DEV IDs only."""

import argparse
from hashlib import sha256
import os
from pathlib import Path

from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.main_cycle import source_unit_metadata
from collusion_handoff.memory import require, strict_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    require(os.getuid() != 0 and Path("/.dockerenv").exists() and os.environ.get("CUDA_VISIBLE_DEVICES") == "" and
            os.environ.get("NVIDIA_VISIBLE_DEVICES") == "void" and not list(Path("/dev").glob("nvidia[0-9]*")) and
            {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}, "Nonroot CPU Docker without GPU/network required")
    require(not args.output_dir.exists(), "Source inventory is immutable; preserve every prior output")
    data = args.source.read_bytes()
    require(sha256(data).hexdigest() == args.expected_sha256, "Pinned native source changed")
    ids, dev = [], {}
    for line in data.decode().splitlines():
        row = strict_json(line)
        number = row["id"]
        require(type(number) is int and number >= 0, "Unexpected pinned source ID type")
        ids.append(number)
        if 0 <= number <= 19:
            dev[number] = {"base_person": row["base_person"]}
    require(len(set(ids)) == len(ids), "Repeated source IDs in pinned data")
    os.umask(0o077)
    atomic_json(args.output_dir / "all-source-ids.json", {"schema": "pinned_source_id_inventory_v1", "source_sha256": args.expected_sha256,
        "group_ids": sorted(ids), "group_count": len(ids), "outside_DEV_contents_screened": False, "new_model_calls": 0})
    atomic_json(args.output_dir / "DEV-source-units.json", {"schema": "exposed_DEV_base_input_hashes_v1", "source_sha256": args.expected_sha256,
        "requested_DEV_ID_range": [0, 19], "actual_existing_DEV_ids": sorted(dev), "future_queries_opened": False,
        "metadata": source_unit_metadata(dev, sorted(dev)), "new_model_calls": 0})
    atomic_json(args.output_dir / "receipt.json", {"schema": "source_id_and_DEV_unit_metadata_receipt_v1", "new_model_calls": 0,
        "main_frame_opened": False, "main_eligibility_screened": False, "source_sha256": args.expected_sha256,
        "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "file_sha256": {path.name: sha256(path.read_bytes()).hexdigest() for path in args.output_dir.glob("*.json")}})


if __name__ == "__main__":
    main()
