"""Complete only missing neutral indexes in an isolated post-prefix CPU window."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from collusion_handoff import main_cycle
from collusion_handoff.artifacts import atomic_json, fingerprint, read_json


INDEXER_SHA = "11d65bed615618fa9a1362dca5484db91ef9f430cb2fd98acf5ec407c6687e93"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "code", "root", "indexer", "work"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    require = main_cycle.require
    require(os.getuid() != 0 and Path("/.dockerenv").exists() and
            os.environ.get("CUDA_VISIBLE_DEVICES") == "" and
            os.environ.get("NVIDIA_VISIBLE_DEVICES") == "void" and
            not list(Path("/dev").glob("nvidia[0-9]*")) and
            {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"},
            "Source index completion requires isolated nonroot CPU Docker")
    manifest = read_json(args.manifest)
    require(fingerprint(manifest) == args.manifest_sha256 and
            manifest["sample"]["prefix_task_indices"] == [1, 2], "Wrong frozen main manifest")
    require(Path(main_cycle.__file__).resolve() == (args.code / "src/collusion_handoff/main_cycle.py").resolve(),
            "Use the immutable main validator imports")
    inventory = main_cycle.verify_code_inventory(args.code, manifest["pipeline"]["file_sha256"])
    require(main_cycle.file_hash(args.indexer) == INDEXER_SHA, "Original isolated indexer changed")
    require(not (args.root / "STOP").exists() and not (args.root / "continuations").exists(),
            "STOP or downstream memory collection prohibits this source-only window")
    receipt_path = args.work / "index-completion-receipt.json"
    require(not receipt_path.exists(), "Preserve the previous CPU receipt")
    numbers = manifest["sample"]["group_ids"]
    require(len(numbers) == len(set(numbers)) == manifest["sample"]["assigned_group_count"],
            "Assigned inventory changed")
    # Check every historical prefix before creating even the first missing index.
    for number in numbers:
        status = args.root / f"prefixes/prefix/group-{number}/status.json"
        require(read_json(status)["status"] == "prefix_collected", "Not all assigned prefixes are complete")
    created, existing, rows = [], [], {}
    for number in numbers:
        prefix = args.root / f"prefixes/prefix/group-{number}"
        index = args.root / f"source-review-index/group-{number}.json"
        if not index.exists():
            subprocess.run([sys.executable, "-B", str(args.indexer), "--source", str(prefix / "source.json"),
                            "--output", str(index), "--manifest", str(args.manifest),
                            "--manifest-sha256", args.manifest_sha256, "--prefix-status", str(prefix / "status.json")], check=True)
            created.append(number)
        else:
            existing.append(number)
        binding = read_json(index)
        require(binding["source_file_sha256"] == main_cycle.file_hash(prefix / "source.json") and
                binding["archive_sha256"] == read_json(prefix / "source.json")["archive_sha256"],
                "An existing neutral index no longer binds its unchanged source")
        rows[str(number)] = {"index_sha256": main_cycle.file_hash(index),
                             "source_file_sha256": binding["source_file_sha256"]}
    receipt = {"schema": "main_source_index_completion_v1", "status": "passed_all_assigned_sources",
               "manifest_sha256": args.manifest_sha256, "code_commit": manifest["pipeline"]["code_commit"],
               "actual_code_inventory_sha256": inventory, "indexer_sha256": INDEXER_SHA,
               "new_index_group_ids": created, "existing_index_group_ids": existing, "groups": rows,
               "new_model_calls": 0, "GPU_devices": [], "network_interfaces": ["lo"],
               "human_gold": False, "semantic_labels_created": False}
    atomic_json(receipt_path, receipt)
    print({"status": receipt["status"], "assigned_count": len(rows), "new_indexes": created, "new_model_calls": 0})


if __name__ == "__main__":
    main()
