from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from collusion_handoff.city_labels import VERSION, map_initial_destination, resolve_initial_city
from collusion_handoff.memory import MemoryError


class CityLabelTests(unittest.TestCase):
    def test_literal_canonical_parentheses_take_priority(self):
        result = resolve_initial_city("Hotel (Suite), City(Region)", {"City", "City(Region)"}, [], lambda value: value.split("(")[0])
        self.assertEqual(result["city"], "City(Region)")
        self.assertEqual(result["mapping_mode"], "exact_canonical_suffix")

    def test_unicode_hotel_commas_and_parentheses_are_preserved(self):
        source = "和缘, Hotel (Suite), 東京(関東)"
        result = resolve_initial_city(source, {"東京"}, [{"city": "東京", "state": "関東"}], lambda value: value.split("(")[0])
        self.assertEqual(result["city"], "東京")
        self.assertEqual(result["source_accommodation"], source)
        self.assertEqual(result["source_accommodation_sha256"], sha256(source.encode()).hexdigest())
        self.assertEqual(result["region_annotation"], "関東")

    def test_ambiguous_suffix_is_not_disambiguated_by_region(self):
        with self.assertRaises(MemoryError):
            resolve_initial_city("Hotel, Zone, City(Region)", {"City", "Zone, City"}, [{"city": "City", "state": "Region"}], lambda value: value.split("(")[0])

    def test_unknown_region_and_missing_first_day_fail(self):
        for source in ("Hotel, City(Unknown)", "-", "Hotel City(Region)", "Hotel, City(Region) extra"):
            with self.subTest(source=source), self.assertRaises(MemoryError):
                resolve_initial_city(source, {"City"}, [{"city": "City", "state": "Region"}], lambda value: value.split("(")[0])

    def test_punctuation_in_city_remains_exact(self):
        result = resolve_initial_city("Hotel, St. John's(Region)", {"St. John's"}, [{"city": "St. John's", "state": "Region"}], lambda value: value.split("(")[0])
        self.assertEqual(result["city"], "St. John's")

    def test_hash_pins_are_checked_before_loading_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            table, helper = root / "cities.tsv", root / "func.py"
            table.write_text("City\tRegion\n")
            helper.write_text("def extract_before_parenthesis(value):\n    return value.split('(')[0]\n")
            policy = {"version": VERSION, "city_state_file": str(table), "city_state_sha256": sha256(table.read_bytes()).hexdigest(),
                      "upstream_helper_file": str(helper), "upstream_helper_sha256": sha256(helper.read_bytes()).hexdigest()}
            self.assertEqual(map_initial_destination("Hotel, City(Region)", {"City"}, policy)["city"], "City")
            table.write_text("City\tOther\n")
            with self.assertRaises(MemoryError):
                map_initial_destination("Hotel, City(Region)", {"City"}, policy)


if __name__ == "__main__":
    unittest.main()
