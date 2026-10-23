"""Check real native imports and canonical tools without any model calls."""

from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path


def main():
    if os.getuid() == 0:
        raise RuntimeError("Checks require non-root Docker")
    os.umask(0o077)
    from env.env_systems.travel_planner_env.clients.openai_client import OpenAIClient
    from env.env_systems.web_shopping_env.runtime import runtime_paths
    from env.env_systems.travel_planner_env.tool_executor import ToolExecutor
    import pandas

    root = Path("/opt/ai-collusion/sources/MemoryArena")
    assert runtime_paths.MEMORYARENA_ROOT == root
    assert runtime_paths.WORKSPACE_ROOT == Path("/opt/ai-collusion")
    assert Path(pandas.__file__).is_relative_to("/vendor")
    expected = {
        "pandas": "2.2.3", "pytz": "2025.2", "tzdata": "2025.2",
        "numpy": "2.2.6", "python-dateutil": "2.9.0.post0", "six": "1.17.0",
        "vllm": "0.32.0", "openai": "3.35.1", "transformers": "5.19.0",
    }
    installed = {name: version(name) for name in expected}
    assert installed == expected, installed
    executor = ToolExecutor(db_path="/database")
    result = executor.execute("AccommodationSearch", {"city": "Rockford"})
    assert result and not result.startswith("Error"), result
    assert "There is no attraction" not in result, result
    Path("/work/accommodation-result.txt").write_text(result, encoding="utf-8")
    identity = {
        "model_calls": 0,
        "native_client": OpenAIClient.__module__,
        "memoryarena_root": str(runtime_paths.MEMORYARENA_ROOT),
        "workspace_root": str(runtime_paths.WORKSPACE_ROOT),
        "package_versions": installed,
        "lock_sha256": sha256(Path("/code/configs/travel-tools.lock").read_bytes()).hexdigest(),
        "tool": "AccommodationSearch", "arguments": {"city": "Rockford"},
        "result_sha256": sha256(result.encode()).hexdigest(),
    }
    Path("/work/travel-tools-identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    print(json.dumps(identity, indent=2), flush=True)


if __name__ == "__main__":
    main()
