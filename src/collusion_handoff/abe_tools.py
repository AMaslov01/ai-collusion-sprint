"""The same immutable catalog and ID-only ledger tools for both actor roles."""

from copy import deepcopy

from .catalog import decimal_value
from .memory import require


def execute_tool(protocol, name, arguments, *, page_size=12):
    if name == "catalog_search":
        lower, upper = arguments["minimum_price"], arguments["maximum_price"]
        rows = [row for row in protocol.catalog.values() if row["city"] == arguments["city"]
                and (lower is None or decimal_value(row["price"]) >= decimal_value(lower))
                and (upper is None or decimal_value(row["price"]) <= decimal_value(upper))]
        rows.sort(key=lambda row: row["row_index"])
        offset = arguments["offset"]
        require(type(offset) is int and offset >= 0, "Invalid catalog pagination")
        return {"total_matches": len(rows), "offset": offset, "page_size": page_size,
                "items": deepcopy(rows[offset:offset+page_size])}
    if name == "catalog_get":
        row = protocol.catalog.get(arguments["catalog_item_id"])
        return {"item": deepcopy(row), "error": None if row is not None else "unknown_catalog_id"}
    if name == "ledger_lookup":
        return {"committed_plans": protocol.lookup(arguments["traveler_id"])}
    raise ValueError("Unknown allowed tool")
