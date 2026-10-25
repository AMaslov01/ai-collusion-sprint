"""Reject mutable pre-treatment metadata before cache replay or analysis begins."""

from hashlib import sha256
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.paired_analysis import AnalysisError


ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location("provenance_" + name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


class MainProvenanceTests(unittest.TestCase):
    def test_consistent_replacement_CPU_receipt_cannot_replace_the_original_inference_receipt(self):
        cache = script("audit-main-cache")
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            run, review, code = base / "run", base / "review", base / "code"
            manifest = {"sample": {"group_ids": []}, "fixture": "Synthetic metadata, no native source"}
            atomic_json(run / "main-manifest.json", manifest)
            annotation = review / "ai-source-annotations.json"
            atomic_json(annotation, {"synthetic_label": "original locked source label"})
            for phase in ("prefixes", "continuations"):
                pre = {"manifest_sha256": fingerprint(manifest), "phase": phase, "status": "passed_no_model_calls",
                       "files": {}, "prepared_files_sha256": {}, "review_files_sha256":
                       {} if phase == "prefixes" else {annotation.name: digest(annotation)}}
                atomic_json(run / ("cpu-" + phase) / "preflight.json", pre)
                atomic_json(run / phase / "phase-manifest.json", {"manifest_sha256": fingerprint(manifest),
                            "CPU_preflight_sha256": digest(run / ("cpu-" + phase) / "preflight.json")})
            atomic_json(annotation, {"synthetic_label": "different but internally consistent source label"})
            path = run / "cpu-continuations/preflight.json"
            changed = read_json(path)
            changed["review_files_sha256"][annotation.name] = digest(annotation)
            atomic_json(path, changed)
            argv = ["audit", "--run", str(run), "--review", str(review), "--inference-codepath", str(code),
                    "--prefix-preflight", str(run / "cpu-prefixes"), "--continuation-preflight", str(run / "cpu-continuations"),
                    "--output", str(base / "audit-output")]
            with patch.object(sys, "argv", argv), patch.object(sys, "path", list(sys.path)), \
                 patch("collusion_handoff.main_cycle.approved_pipeline", return_value=(manifest, {}, {}, {}, {})), \
                 patch("collusion_handoff.main_cycle.prefix_bundle", side_effect=AssertionError("Must reject before native state")):
                with self.assertRaisesRegex(RuntimeError, "Supplied CPU receipt differs"):
                    cache.main()

    def test_analysis_rejects_phase_or_annotation_mutation_after_a_successful_audit(self):
        analysis = script("analyze-main")
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            run, review = base / "run", base / "review"
            phase = run / "continuations/phase-manifest.json"
            annotation = review / "ai-source-annotations.json"
            atomic_json(phase, {"synthetic_phase": "original"})
            atomic_json(annotation, {"synthetic_mask": "locked"})
            manifest = {"analysis": {"analysis_code_sha256": digest(ROOT / "scripts/analyze-main.py")}}
            audit = {"schema": "main_cache_replay_v1", "status": "passed_cache_only", "manifest_sha256": fingerprint(manifest),
                     "new_model_calls": 0, "review_files_sha256": {annotation.name: digest(annotation)},
                     "input_sha256": {str(phase.relative_to(run)): digest(phase)}}
            audit_path = base / "audit.json"
            atomic_json(audit_path, audit)
            argv = ["analysis", "--run", str(run), "--review", str(review), "--cache-audit", str(audit_path), "--output", str(base / "result.json")]
            with patch.object(sys, "argv", argv), patch.object(analysis, "approved_pipeline", return_value=(manifest, {}, {}, {}, {})), \
                 patch.object(analysis, "analyze", side_effect=AssertionError("Must reject before arithmetic")):
                for target in (phase, annotation):
                    original = target.read_bytes()
                    atomic_json(target, {"synthetic_mutation": "after successful audit"})
                    with self.assertRaises(AnalysisError):
                        analysis.main()
                    target.write_bytes(original)

    def test_another_executing_analysis_driver_is_rejected_before_opening_the_audit(self):
        analysis = script("analyze-main")
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            argv = ["analysis", "--run", str(base / "run"), "--review", str(base / "review"),
                    "--cache-audit", str(base / "missing-audit.json"), "--output", str(base / "result.json")]
            with patch.object(sys, "argv", argv), patch.object(analysis, "approved_pipeline", return_value=(
                    {"analysis": {"analysis_code_sha256": "0" * 64}}, {}, {}, {}, {})), \
                 patch.object(analysis, "read_json", side_effect=AssertionError("Must check driver before audit")):
                with self.assertRaisesRegex(AnalysisError, "Executing analysis driver differs"):
                    analysis.main()


if __name__ == "__main__":
    unittest.main()
