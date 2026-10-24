"""Construct locked new component wires without opening fresh native sources."""

import os
from pathlib import Path

from transformers import AutoTokenizer

from collusion_handoff.artifacts import atomic_json, fingerprint
from collusion_handoff.memory import require
from collusion_handoff.memory_v3 import fixture_source, fixture_eligible, fixture_mapping
from collusion_handoff.readiness_cycle import fresh_definitions, payload, settings, fixture_input


def main():
    require(os.getuid() != 0 and os.environ.get("CUDA_VISIBLE_DEVICES") == "", "Non-root CPU-only Docker required")
    config, _, assets = settings()
    tokens = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    probes, summaries = {}, {}
    for definition in fresh_definitions(config):
        source, candidates = fixture_input(config, definition)
        for stage, selected in (("V", candidates), ("P", fixture_eligible(definition, candidates))):
            body = payload(stage, source, selected, config, assets)
            key = definition["fixture_id"] + "/" + stage
            count = len(tokens.apply_chat_template(body["messages"], tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=False))
            require(count + body["max_tokens"] <= 32768, "Fresh fixture admission overflow")
            probes[key] = body
            summaries[key] = {"request_sha256": fingerprint(body), "rendered_input_tokens": count, "output_reserve": body["max_tokens"],
                              "candidate_ids": [item["claim_id"] for item in selected]}
    atomic_json(Path("/work/fresh-component-wire-probes.json"), probes)
    atomic_json(Path("/work/fresh-component-wire-summary.json"), {"status": "constructed_no_model_calls", "probes": summaries,
                "source_native_groups6_7_opened": False, "new_model_calls": 0, "lock_sha256": config["fresh_fixtures"]["lock_sha256"],
                "wire_probe_count": len(probes), "expected_labels_in_model_payloads": False})


if __name__ == "__main__":
    main()
