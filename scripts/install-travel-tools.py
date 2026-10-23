"""Install only hash-pinned missing tool dependencies into an external directory."""

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    if os.getuid() == 0:
        raise RuntimeError("Installation requires non-root Docker")
    os.umask(0o077)
    vendor = Path("/vendor")
    if any(vendor.iterdir()):
        raise RuntimeError("Refusing to modify a non-empty dependency directory")
    wheels = Path("/work/wheels")
    wheels.mkdir(mode=0o700)
    for item in json.loads(Path("/code/configs/travel-tools.wheels.json").read_text()):
        destination = wheels / item["filename"]
        subprocess.run([
            "curl", "--fail", "--location", "--silent", "--show-error",
            "--proto", "=https", "--limit-rate", "5000k", "--max-time", "900",
            "--output", str(destination), item["url"],
        ], check=True)
        if sha256(destination.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Wheel checksum mismatch: {destination.name}")
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--target", str(vendor),
        "--no-deps", "--require-hashes", "--no-compile", "--only-binary=:all:",
        "--no-index", "--find-links", str(wheels), "--disable-pip-version-check",
        "-r", "/code/configs/travel-tools.lock",
    ], check=True)


if __name__ == "__main__":
    main()
