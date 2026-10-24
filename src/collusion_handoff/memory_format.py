"""Explicit opt-in decoder variant; the original strict decoder is unchanged."""

from hashlib import sha256
import re

from .memory import require, validate


VERSION = "pure_json_or_single_external_json_fence_v1"
OUTER = re.compile(r"\A[ \t\r\n]*```json[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t\r\n]*\Z", re.DOTALL)


def decode_format(text, schema):
    wrapper = OUTER.fullmatch(text)
    if wrapper:
        require(text.count("```") == 2, "More than one JSON fence pair; no recovery allowed")
        body = wrapper.group("body")  # Exact substring, without strip/replace/normalization.
        mode = "one_external_json_fence_removed"
    else:
        body, mode = text, "pure_json_unmodified"
    value = validate(body, schema)
    return value, {"decoder_version": VERSION, "mode": mode,
                   "raw_content_sha256": sha256(text.encode()).hexdigest(),
                   "validated_substring_sha256": sha256(body.encode()).hexdigest(),
                   "raw_utf8_bytes": len(text.encode()), "validated_utf8_bytes": len(body.encode()),
                   "interior_text_modified": False, "strict_json_and_schema_validated": True}
