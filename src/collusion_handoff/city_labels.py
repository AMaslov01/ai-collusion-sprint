"""Exact first-day destination labels, with pinned upstream region notation."""

from hashlib import sha256
import importlib.util
from pathlib import Path
import re

from .memory import require


VERSION = "pinned_terminal_city_state_v1"


def resolve_initial_city(accommodation, cities, city_state_pairs, normalize):
    require(isinstance(accommodation, str), "Missing first-day accommodation label")
    cities = set(cities)
    exact = [city for city in cities if accommodation.endswith(", " + city)]
    require(len(exact) <= 1, "Ambiguous exact canonical destination suffix")
    region = None
    if exact:
        city, mode = exact[0], "exact_canonical_suffix"
        label = city
    else:
        match = re.search(r"\(([^()]+)\)$", accommodation)
        require(match is not None, "No exact canonical city or terminal region notation")
        prefix, region = accommodation[:match.start()], match[1]
        candidates = [city for city in cities if prefix.endswith(", " + city)]
        require(len(candidates) == 1, "Region notation has no unique exact canonical city")
        city = candidates[0]
        require("(" not in city and ")" not in city, "Nested or repeated city-region notation")
        require(sum(pair["city"] == city and pair["state"] == region for pair in city_state_pairs) == 1,
                "City-region pair is not uniquely present in the pinned table")
        label = city + "(" + region + ")"
        require(normalize(label) == city, "Pinned upstream city normalizer disagrees")
        mode = "pinned_terminal_region_annotation"
    return {"normalization_version": VERSION, "mapping_mode": mode, "city": city,
            "source_accommodation": accommodation, "source_accommodation_sha256": sha256(accommodation.encode()).hexdigest(),
            "source_city_label": label, "region_annotation": region, "selected_day_index": 1,
            "geography_basis": "Pinned benchmark rendering, not external geographic validation"}


def pinned_dependencies(policy):
    require(policy["version"] == VERSION, "Unknown city normalization version")
    table, helper_file = Path(policy["city_state_file"]), Path(policy["upstream_helper_file"])
    require(sha256(table.read_bytes()).hexdigest() == policy["city_state_sha256"], "Pinned city-state table changed")
    require(sha256(helper_file.read_bytes()).hexdigest() == policy["upstream_helper_sha256"], "Pinned city helper changed")
    pairs = []
    for line in table.read_text().splitlines():
        values = line.split("\t")
        require(len(values) == 2 and all(values), "Malformed pinned city-state row")
        pairs.append({"city": values[0], "state": values[1]})
    require(len({(item["city"], item["state"]) for item in pairs}) == len(pairs), "Duplicate pinned city-state pair")
    # The pinned file imports only stdlib and defines functions; do not import env.__init__.
    spec = importlib.util.spec_from_file_location("pinned_travel_city_normalizer", helper_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return pairs, module.extract_before_parenthesis


def map_initial_destination(accommodation, cities, policy):
    pairs, normalize = pinned_dependencies(policy)
    receipt = resolve_initial_city(accommodation, cities, pairs, normalize)
    receipt.update({"city_state_sha256": policy["city_state_sha256"], "upstream_helper_sha256": policy["upstream_helper_sha256"]})
    return receipt
