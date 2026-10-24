import json
from pathlib import Path
import unittest
from jsonschema.exceptions import ValidationError

from collusion_handoff.memory import validate
from collusion_handoff.memory_format import decode_format


SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "schemas/memory-summary.proposed.json").read_text())


class FormatVariantTests(unittest.TestCase):
    def test_original_decoder_still_rejects_fences(self):
        with self.assertRaises(ValueError):
            validate('```json\n{"sentences":["A source fact."]}\n```', SCHEMA)

    def test_fence_variant_preserves_exact_inner_whitespace_and_values(self):
        inner = '  {"sentences": ["A  source fact."]}  '
        parsed, receipt = decode_format(' \n```json\n' + inner + '\n```\t', SCHEMA)
        self.assertEqual(parsed["sentences"], ["A  source fact."])
        self.assertEqual(receipt["validated_utf8_bytes"], len(inner.encode()))
        self.assertFalse(receipt["interior_text_modified"])

    def test_pure_json_is_unmodified(self):
        _, receipt = decode_format(' {"sentences":["A source fact."]}\n', SCHEMA)
        self.assertEqual(receipt["mode"], "pure_json_unmodified")
        self.assertEqual(receipt["raw_content_sha256"], receipt["validated_substring_sha256"])

    def test_nonexact_wrappers_and_malformed_inner_are_rejected(self):
        for text in ('prefix```json\n{}\n```', '```json\n{}\n```suffix', '```python\n{}\n```',
                     '```json\n{"sentences":["x"],"sentences":["y"]}\n```',
                     '```json\n{"sentences":["x"]}\n```\n```json\n{}\n```',
                     '```json\n{"sentences":[]}\n```'):
            with self.subTest(text=text), self.assertRaises((ValueError, ValidationError)):
                decode_format(text, SCHEMA)


if __name__ == "__main__":
    unittest.main()
