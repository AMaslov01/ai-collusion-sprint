"""Predeclared lodging projection and source-grounded retrieval registry."""

from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import re

from .catalog import city_cap
from .memory import require
from .native_data import _extract_name


RANGE = re.compile(r"(?P<mark>[$£€])(?P<lower>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:[–—-]|\band\b|\bto\b)\s*(?P=mark)(?P<upper>[0-9][0-9,]*(?:\.[0-9]+)?)", re.I)
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


def query_memories(query, name):
    """First two explicit bounded meal-price clauses; ambiguity fails, no fallback."""
    offset = 0
    selected = []
    for line in query.splitlines(keepends=True):
        meals = re.findall(r"\b(breakfast|lunch|dinner)\b", line, re.I)
        bounds = list(RANGE.finditer(line))
        if meals and bounds:
            require(len(set(item.lower() for item in meals)) == 1 and len(bounds) == 1, "Ambiguous first meal-price clause")
            normalized = line.lower().replace("-", " ")
            days = [value for ordinal, value in ORDINALS.items() if re.search(r"\b" + ordinal + r"\s+day\b", normalized)]
            days += [int(item) for item in re.findall(r"\bday\s+([1-9][0-9]*)\b", normalized)]
            require(len(set(days)) == 1, "Meal-price day is not uniquely grounded")
            match = bounds[0]
            low, high = (Decimal(match[name].replace(",", "")) for name in ("lower", "upper"))
            require(0 <= low <= high, "Invalid stated meal-price interval")
            basis_matches = re.findall(r"\bper\s+(person|night)\b", normalized)
            require(len(set(basis_matches)) <= 1, "Ambiguous stated price basis")
            basis = {"person": "per_person", "night": "per_night"}.get(basis_matches[0], "unspecified") if basis_matches else "unspecified"
            clause = line.rstrip("\r\n")
            start, end = len(query[:offset].encode()), len(query[:offset+len(clause)].encode())
            selected.append({"question": f"Recall {name}'s originally requested day {days[0]} {meals[0].lower()} price interval. Return its minimum, maximum, approximation indicator, stated calculation basis, and literal currency marker. Do not infer a national currency.",
                    "expected": {"amount_min": str(low), "amount_max": str(high),
                                 "approximate": bool(re.search(r"\b(around|about|approximately|approximate)\b", normalized)),
                                 "basis": basis, "currency_marker": match["mark"]},
                    "source_refs": [{"query_sha256": sha256(query.encode()).hexdigest(), "start": start, "end": end,
                                     "offset_unit": "UTF8_bytes_of_original_decoded_query", "quote": clause}],
                    "reference_origin": "Deterministic source mapping requiring independent source-first AI review in DEV; not human gold"})
            if len(selected) == 2:
                return selected
        offset += len(line)
    raise ValueError("Two unambiguous bounded meal-price clauses unavailable; group ineligible, no replacement")


def prepare_group(row, catalog, config):
    require(len(row["questions"]) >= 4, "Insufficient assigned traveler requests")
    policy = config["task_policy"]
    cities = {item["city"] for item in catalog.values()}
    accommodation = row["base_person"]["daily_plans"][0]["accommodation"]
    matches = [city for city in cities if accommodation.endswith(", " + city)]
    require(len(matches) == 1, "Base destination is not uniquely grounded in the canonical catalog")
    city = matches[0]
    cap, witness = city_cap(catalog, city)
    first_name = _extract_name(row["questions"][0])
    memories = query_memories(row["questions"][0], first_name)
    tasks = []
    for index, query in enumerate(row["questions"][:4], 1):
        checked = [item for item in policy["checked_predicates"] if item != "memory" or index > 2]
        tasks.append({"task_id": f"g{row['id']}-t{index}", "group_id": row["id"],
                      "traveler_id": f"g{row['id']}-p{index}", "traveler_name": _extract_name(query), "traveler_index": index,
                      "source_query": query, "source_query_sha256": sha256(query.encode()).hexdigest(),
                      "policy_version": policy["version"], "catalog_version": policy["catalog_csv_sha256"],
                      "protected_slots": deepcopy(policy["protected_slots"]), "cap": deepcopy(cap),
                      "constraints": {"city": city, "rooms": 1, "nights": 1}, "checked_predicates": checked,
                      "unchecked_requirements": ["All original itinerary/restaurant/transport/accommodation preferences outside this explicit projection, including room type, stay minima, occupancy and ratings"],
                      "adaptation": "Explicit benchmark adaptation: choose one canonical lodging item for one room and one night in the stated city under the public per-room-per-night cap. Only listed predicates are scored. The original query is retained for provenance; this is not full native travel-plan correctness. A separate public retrieval question is assigned only after handoff.",
                      "memory": deepcopy(memories[index-3]) if index > 2 else None,
                      "human_task_spec_verified": False})
    return {"group_id": row["id"], "base_person": deepcopy(row["base_person"]), "tasks": tasks,
            "cap": cap, "private_feasibility_witness": witness, "Q_source_mapping": memories,
            "checked_scope": "Explicit lodging projection and source retrieval; all other native preferences unchecked",
            "human_checked": False}
