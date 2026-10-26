"""Recompute frozen statistics from the public numeric projection, without LLMs.

This checks arithmetic against the frozen report. It does not authenticate the
withheld raw conversations or repeat the production cache replay.
"""
import argparse
import json
from pathlib import Path

from public_checks import require_isolated_cpu
from collusion_handoff.artifacts import read_json
from collusion_handoff.main_analysis import analyze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=Path("/code/release/frozen/analysis-inputs.json"))
    parser.add_argument("--expected", type=Path, default=Path("/code/release/frozen/analysis.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require_isolated_cpu()
    if args.output.exists():
        raise ValueError("Use a new output file")
    inputs, expected = read_json(args.inputs), read_json(args.expected)
    if inputs["schema"] != "memcontrast_public_analysis_inputs_v1":
        raise ValueError("Unexpected numeric projection")
    report = analyze(inputs["manifest"], inputs["assignments"], inputs["outcomes"], inputs["annotation"],
                     {int(k): v for k, v in inputs["prefix_H"].items()})
    if any(expected.get(key) != value for key, value in report.items()):
        raise ValueError("Recomputed statistics differ from frozen report")
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "all_frozen_analysis_fields_equal", "new_model_calls": 0,
                      "scope": "numeric_projection_arithmetic_only", "numpy_version": __import__("numpy").__version__}))


if __name__ == "__main__":
    main()
