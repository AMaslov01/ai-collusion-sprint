"""Verify pinned task data and report IDs/counts; do not select or run jobs."""

import argparse
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import subprocess

from huggingface_hub import HfApi


def main():
    if os.getuid() == 0:
        raise RuntimeError("Requires non-root Docker on caniculus")
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(Path("/code/configs/dev-package.draft.json").read_text())
    data = config["data"]
    info = HfApi().dataset_info(data["repo"], revision=data["revision"], files_metadata=True)
    if info.sha != data["revision"]:
        raise RuntimeError("Dataset revision mismatch")
    relative = "group_travel_planner/data.jsonl"
    sibling = next(item for item in info.siblings if item.rfilename == relative)
    args.destination.mkdir(mode=0o700)
    destination = args.destination / "data.jsonl"
    subprocess.run([
        "curl", "--fail", "--location", "--silent", "--show-error",
        "--proto", "=https", "--limit-rate", "5000k", "--max-time", "900",
        "--output", str(destination),
        f"https://huggingface.co/datasets/{data['repo']}/resolve/{info.sha}/{relative}",
    ], check=True)
    content = destination.read_bytes()
    digest = sha256(content).hexdigest()
    if sibling.size is not None and len(content) != sibling.size:
        raise RuntimeError("Dataset size mismatch")
    if sibling.lfs is not None:
        if digest != sibling.lfs.sha256:
            raise RuntimeError("Dataset LFS hash mismatch")
    elif sha1(f"blob {len(content)}\0".encode() + content).hexdigest() != sibling.blob_id:
        raise RuntimeError("Dataset Git blob hash mismatch")
    rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line]
    registry = []
    for index, row in enumerate(rows):
        registry.append({
            "upstream_id": row["id"], "zero_based_source_index": index,
            "display_position": index + 1, "new_traveler_requests": len(row["questions"]),
            "answer_count": len(row["answers"]), "has_base_person": bool(row["base_person"]),
        })
    if len({json.dumps(row["upstream_id"]) for row in registry}) != len(registry):
        raise RuntimeError("Upstream group IDs are not unique")
    identity = {
        "repo": data["repo"], "revision": info.sha, "source_file": relative,
        "sha256": digest, "bytes": len(content), "source_blob_id": sibling.blob_id,
        "groups": registry, "model_calls": 0,
        "selection_status": "not_confirmed_no_jobs_authorized_by_this_registry",
    }
    (args.destination / "registry.json").write_text(json.dumps(identity, indent=2) + "\n")
    proposal = {
        "status": "PROPOSED", "basis": "Handoff D04: first 12 display positions only if actual IDs confirmed by the user",
        "groups": registry[:12], "proposed_max_new_travelers": 4,
        "proposed_checkpoint_after": 2, "model_calls": 0,
        "minimum_requests_in_proposed_groups": min(row["new_traveler_requests"] for row in registry[:12]),
    }
    (args.destination / "native-dev-proposal.json").write_text(json.dumps(proposal, indent=2) + "\n")
    print(json.dumps({"dataset_sha256": digest, "group_count": len(rows), "proposal": proposal}, indent=2), flush=True)


if __name__ == "__main__":
    main()
