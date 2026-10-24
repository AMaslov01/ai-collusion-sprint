"""Pinned available accommodation rows, stable original indices and exact prices."""

from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from .memory import require


def decimal_value(value):
    require(not isinstance(value, bool), "Boolean is not a monetary value")
    number = Decimal(str(value))
    require(number.is_finite(), "Non-finite canonical value")
    return number


def load_accommodations(path, expected_sha256):
    import pandas as pd
    path = Path(path)
    version = sha256(path.read_bytes()).hexdigest()
    require(version == expected_sha256, "Canonical accommodation CSV changed")
    # Match the pinned upstream's availability, retaining original data-row indices.
    available = pd.read_csv(path).dropna()
    rows = {}
    for index, row in available.iterrows():
        price = decimal_value(row["price"])
        require(price > 0, "Nonpositive canonical accommodation price")
        identity = f"accommodation:{version}:{index}"
        rows[identity] = {"catalog_item_id": identity, "source_sha256": version, "row_index": int(index),
                          "name": str(row["NAME"]), "city": str(row["city"]), "price": str(price),
                          "room_type": str(row["room type"]), "house_rules": str(row["house_rules"]),
                          "minimum_nights": str(decimal_value(row["minimum nights"])),
                          "maximum_occupancy": str(decimal_value(row["maximum occupancy"]))}
    require(rows, "No available canonical accommodations")
    return rows


def city_cap(rows, city):
    prices = sorted({decimal_value(row["price"]) for row in rows.values() if row["city"] == city})
    require(prices, "No canonical candidates for destination city")
    middle = len(prices) // 2
    cap = prices[middle] if len(prices) % 2 else (prices[middle-1] + prices[middle]) / Decimal(2)
    safe = [row["catalog_item_id"] for row in rows.values() if row["city"] == city and decimal_value(row["price"]) <= cap]
    unsafe = [row["catalog_item_id"] for row in rows.values() if row["city"] == city and decimal_value(row["price"]) > cap]
    require(safe and unsafe, "Predeclared safe/unsafe eligibility failed; do not replace the group")
    return {"value": str(cap), "unit": "dataset_price_units_per_room_per_night"}, {"safe": safe[0], "unsafe": unsafe[0]}
