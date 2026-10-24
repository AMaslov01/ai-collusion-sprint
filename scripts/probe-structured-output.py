"""CPU-only probe of the pinned vLLM JSON-schema path; never starts a model."""

from dataclasses import fields
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import traceback

from transformers import AutoTokenizer
import xgrammar as xgr

from collusion_handoff.artifacts import atomic_json
from collusion_handoff.abe_protocol import action_schema, bind_review_schema, bind_symbolic_references
from vllm.config.structured_outputs import StructuredOutputsConfig


def main():
    if os.getuid() == 0 or os.environ.get("CUDA_VISIBLE_DEVICES") != "":
        raise RuntimeError("Requires non-root CPU-only Docker")
    schemas = ["memory-summary.proposed.json", "memory-extraction.v3.1.json", "memory-extraction.v4.json", "memory-extraction.v5.json", "memory-verification.v3.json", "memory-policy.proposed.json"]
    result = {"new_model_calls": 0, "gpu_assigned": False,
              "versions": {name: importlib.metadata.version(name) for name in ("vllm", "xgrammar", "transformers")},
              "structured_outputs_config_fields": [item.name for item in fields(StructuredOutputsConfig)],
              "tokenizer_info_signature": str(inspect.signature(xgr.TokenizerInfo.from_huggingface)),
              "compile_json_schema_signature": str(inspect.signature(xgr.GrammarCompiler.compile_json_schema)),
              "schemas": {}}
    tokens = AutoTokenizer.from_pretrained("/model", local_files_only=True)
    info = xgr.TokenizerInfo.from_huggingface(tokens)
    compiler = xgr.GrammarCompiler(info, max_threads=1)
    for name in schemas:
        try:
            schema = json.loads((Path("/code/schemas") / name).read_text())
            compiler.compile_json_schema(json.dumps(schema), strict_mode=True)
            result["schemas"][name] = {"status": "compiled_with_xgrammar_strict_mode"}
        except Exception:
            result["schemas"][name] = {"status": "failed", "traceback": traceback.format_exc()}
    for role in ("A", "B"):
        for allow_tools in (True, False):
            name = f"actor-{role}-tools-{allow_tools}"
            try:
                compiler.compile_json_schema(json.dumps(action_schema(role, tools_allowed=allow_tools)), strict_mode=True)
                result["schemas"][name] = {"status": "compiled_with_xgrammar_strict_mode"}
            except Exception:
                result["schemas"][name] = {"status": "failed", "traceback": traceback.format_exc()}
    compiler.compile_json_schema(bind_review_schema(action_schema("B", tools_allowed=True),
                                 {"proposal_id": "session-opaque/task/proposal-1", "version": 1, "proposal_hash": "a" * 64}), strict_mode=True)
    result["schemas"]["actor-B-current-transaction"] = {"status": "compiled_with_xgrammar_strict_mode"}
    for role in ("A", "B"):
        for allow_tools in (True, False):
            for observed in (False, True):
                name = f"bound-{role}-tools-{allow_tools}-observed-{observed}"
                try:
                    schema = action_schema(role, tools_allowed=allow_tools)
                    if role == "B":
                        schema = bind_review_schema(schema, {"proposal_id": "session-opaque/task/proposal-1",
                                                            "version": 1, "proposal_hash": "a" * 64})
                    bounded = bind_symbolic_references(schema, receipt_ids=["observed-role/receipt"] if observed else [],
                        proposal_ids=["visible-committed/task/proposal-1"] if observed else [], catalog_version="f" * 64)
                    compiler.compile_json_schema(bounded, strict_mode=True)
                    result["schemas"][name] = {"status": "compiled_with_xgrammar_strict_mode"}
                except Exception:
                    result["schemas"][name] = {"status": "failed", "traceback": traceback.format_exc()}
    atomic_json(Path("/work/probe.json"), result)
    if any(item["status"] == "failed" for item in result["schemas"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
