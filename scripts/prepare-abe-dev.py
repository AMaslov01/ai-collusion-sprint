"""CPU source-only DEV4/5 task presentation, without looking at fresh groups6/7."""

from hashlib import sha256
import json
import os
from pathlib import Path

from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.catalog import city_cap, load_accommodations
from collusion_handoff.memory import require


def main():
    require(os.getuid() != 0, "Non-root CPU Docker required")
    config = read_json(Path("/basecode/configs/dev-package.draft.json"))
    task_file = Path("/tasks/data.jsonl")
    require(sha256(task_file.read_bytes()).hexdigest() == config["data"]["file_hashes"]["sha256"], "Pinned task file changed")
    # Only selected developmental source is retained, never answers or future validation text.
    selected = []
    for line in task_file.read_text().splitlines():
        row = json.loads(line)
        if row["id"] in (4, 5):
            selected.append({"id": row["id"], "base_person": row["base_person"], "questions": row["questions"][:4]})
    atomic_json(Path("/work/dev-task-sources.json"), {"groups": selected, "reference_origin": "Pinned source, no answers; DEV only", "new_model_calls": 0})
    csv = Path("/database/accommodations/clean_accommodations_2022.csv")
    catalog = load_accommodations(csv, sha256(csv.read_bytes()).hexdigest())
    profiles = {}
    for city in sorted({row["city"] for row in catalog.values()}):
        try:
            cap, witness = city_cap(catalog, city)
            profiles[city] = {"cap": cap, "witness": witness}
        except ValueError as error:
            profiles[city] = {"ineligible": str(error)}
    atomic_json(Path("/work/catalog-profile.json"), {"source_sha256": sha256(csv.read_bytes()).hexdigest(), "available_rows": len(catalog), "cities": profiles})


if __name__ == "__main__":
    main()
