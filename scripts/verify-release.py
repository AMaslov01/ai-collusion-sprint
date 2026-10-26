"""Administrative byte/hash check only; does not recompute scientific results."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args()
root = args.root.resolve()
count = 0
for line in (root / "release/SHA256SUMS").read_text().splitlines():
    digest, name = line.split("  ", 1)
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root) or sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit("Release checksum mismatch: " + name)
    count += 1
frozen = json.loads((root / "release/frozen-source-sha256.json").read_text())
for name, digest in frozen["files"].items():
    path = root / name
    if sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit("Frozen scientific source changed: " + name)
print(json.dumps({"status": "verified_release_bytes", "release_files": count,
                  "frozen_source_files": len(frozen["files"]), "frozen_commit": frozen["commit"]}))
