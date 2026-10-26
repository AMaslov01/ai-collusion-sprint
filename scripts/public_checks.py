"""Shared boundary for public CPU examples and tests; no model endpoint."""
import os
from pathlib import Path


def require_isolated_cpu():
    if (os.getuid() == 0 or not Path("/.dockerenv").exists()
            or os.environ.get("CUDA_VISIBLE_DEVICES") != ""
            or list(Path("/dev").glob("nvidia[0-9]*"))
            or {p.name for p in Path("/sys/class/net").iterdir()} != {"lo"}):
        raise RuntimeError("Requires nonroot CPU Docker, no GPU, CUDA_VISIBLE_DEVICES='', network=none")
