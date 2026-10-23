"""Download and verify the approved snapshot inside non-root Docker; no inference."""

import argparse
from hashlib import sha1, sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys

from huggingface_hub import HfApi, snapshot_download


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    args = parser.parse_args()
    if os.getuid() == 0:
        raise RuntimeError("Must run as non-root in Docker")
    config = json.loads(args.config.read_text())
    if version("vllm") != config["runtime"]["vllm_version"]:
        raise RuntimeError("Image does not contain the approved vLLM version")
    model = config["model"]
    if not any(row["id"] == "D06" and row["status"] == "CONFIRMED" for row in config["decision_register"]):
        raise RuntimeError("Model download requires the recorded user decision")
    metadata = HfApi().model_info(model["repo"], revision=model["revision"], files_metadata=True)
    if metadata.sha != model["revision"]:
        raise RuntimeError("HF model revision mismatch")
    args.destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshot_download(
        repo_id=model["repo"], revision=model["revision"],
        local_dir=args.destination, max_workers=1,
    )
    files = []
    for sibling in metadata.siblings:
        path = args.destination / sibling.rfilename
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Missing/non-regular model file: {sibling.rfilename}")
        size = path.stat().st_size
        if sibling.size is not None and size != sibling.size:
            raise RuntimeError(f"Size mismatch: {sibling.rfilename}")
        digest = sha256()
        git_blob = sha1(f"blob {size}\0".encode())
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
                git_blob.update(chunk)
        if sibling.lfs is not None:
            if digest.hexdigest() != sibling.lfs.sha256:
                raise RuntimeError(f"LFS checksum mismatch: {sibling.rfilename}")
        elif sibling.blob_id is not None and git_blob.hexdigest() != sibling.blob_id:
            raise RuntimeError(f"Git blob mismatch: {sibling.rfilename}")
        files.append({"path": sibling.rfilename, "bytes": size, "sha256": digest.hexdigest()})
    identity = {
        "repo": model["repo"], "revision": metadata.sha, "files": files,
        "versions": {name: version(name) for name in ("vllm", "openai", "httpx", "huggingface-hub", "transformers")},
        "python": sys.version,
    }
    temporary = args.identity.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(identity, output, indent=2, ensure_ascii=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, args.identity)
    print(f"Verified model snapshot: {len(files)} files, {sum(row['bytes'] for row in files)} bytes", flush=True)


if __name__ == "__main__":
    main()
