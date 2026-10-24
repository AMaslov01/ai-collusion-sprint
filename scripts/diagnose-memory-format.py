"""Read-only structure diagnosis; never accept, repair or publish model content."""

from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import time

from collusion_handoff.artifacts import atomic_json, read_json
from collusion_handoff.memory import strict_json, validate
from collusion_handoff.smoke import journal_rows


def main():
    os.umask(0o077)
    if os.getuid() == 0 or time.time() >= 1792840380 or Path("/control/STOP").exists():
        raise RuntimeError("Non-root, live approved deadline and no STOP are required")
    completed, code = Path("/completed"), Path("/code")
    frozen_hash = sha256((completed / "report.json").read_bytes()).hexdigest()
    requests = journal_rows(completed / "io/requests.jsonl")
    responses = journal_rows(completed / "io/responses.jsonl")
    if len(requests) != 1 or len(responses) != 1 or requests[0]["stage"] != "S":
        raise RuntimeError("Expected the single stopped S attempt")
    payload = strict_json(requests[0]["body_utf8"])
    response = strict_json(responses[0]["body_utf8"])
    message = response["choices"][0]["message"]
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("Unexpected non-string S content")
    nonspace = content.lstrip()
    fence = chr(96) * 3
    wrapper = re.fullmatch(r"\s*```(?P<language>[a-zA-Z]*)[^\S\n]*\n(?P<body>.*?)\n```\s*", content, re.DOTALL)
    single_wrapper = bool(wrapper and content.count(fence) == 2)
    inner = {"performed": False, "accepted_as_memory": False, "diagnostic_only": True}
    if single_wrapper:
        inner["performed"] = True
        try:
            parsed = validate(wrapper.group("body"), read_json(code / "schemas/memory-summary.proposed.json"))
            inner.update({"strict_json_and_schema": "would_pass", "root_type": type(parsed).__name__,
                          "root_keys": sorted(parsed), "sentence_array_length": len(parsed["sentences"])})
        except Exception as error:
            inner.update({"strict_json_and_schema": "would_fail", "exception_type": type(error).__name__,
                          "line": getattr(error, "lineno", None), "column": getattr(error, "colno", None)})
    expected_prompt = (code / "prompts/memory/summary.txt").read_text()
    actual_prompt = payload["messages"][0]
    result = {"scope": "native_group1_checkpoint2_read_only_format_diagnosis", "created_at": datetime.now(timezone.utc).isoformat(),
              "diagnostic_code_commit": os.environ["ADAPTER_COMMIT"], "new_model_calls": 0,
              "content_type": type(content).__name__, "content_utf8_bytes": len(content.encode()),
              "content_characters": len(content), "content_empty": not bool(content),
              "content_whitespace_only": not bool(content.strip()),
              "first_nonspace_codepoint": f"U+{ord(nonspace[0]):04X}" if nonspace else None,
              "starts_with_fence": nonspace.startswith(fence), "ends_with_fence": content.rstrip().endswith(fence),
              "fence_delimiter_occurrences": content.count(fence), "single_outer_fence_wrapper": single_wrapper,
              "wrapper_language": wrapper.group("language") if wrapper else None,
              "has_BOM": content.startswith("\ufeff") or nonspace.startswith("\ufeff"),
              "nonwhitespace_prefix_or_suffix_outside_wrapper": False if single_wrapper else None,
              "populated_message_fields": {key: value is not None and value != "" and value != [] for key, value in message.items()},
              "finish_reason": response["choices"][0]["finish_reason"], "usage": response["usage"],
              "actual_S_system_prompt_matches_approved": actual_prompt == {"role": "system", "content": expected_prompt},
              "actual_S_system_prompt_sha256": sha256(actual_prompt["content"].encode()).hexdigest(),
              "diagnostic_inner_validation": inner, "frozen_failure_report_sha256": frozen_hash,
              "official_S_status": "package_build_failure_unchanged", "result_accepted_or_repaired": False,
              "semantic_model_content_exported": False}
    if sha256((completed / "report.json").read_bytes()).hexdigest() != frozen_hash:
        raise RuntimeError("Frozen failure changed")
    atomic_json(Path("/work/format-diagnostics.json"), result)


if __name__ == "__main__":
    main()
