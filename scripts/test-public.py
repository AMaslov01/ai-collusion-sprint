"""Run the data-free suite; upstream client integration is explicitly excluded."""
from pathlib import Path
import sys
import unittest
from public_checks import require_isolated_cpu

require_isolated_cpu()
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "tests"))
loader = unittest.TestLoader()
suite = unittest.TestSuite()
for path in sorted((root / "tests").glob("test_*.py")):
    if path.name != "test_actor_gateway.py":
        suite.addTests(loader.loadTestsFromName(path.stem))
print("Excluded test_actor_gateway.py: requires separately acquired pinned MemoryArena checkout", flush=True)
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
