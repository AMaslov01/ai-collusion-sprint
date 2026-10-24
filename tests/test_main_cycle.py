"""Main orchestration checks using invented tasks and responses, never native IDs."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from collusion_handoff.abe_runner import ServiceFailure
from collusion_handoff.artifacts import atomic_json, fingerprint, read_json
from collusion_handoff.main_cycle import assigned_opportunities, approved_pipeline, continuation_group, file_hash, registry_digest, verify_annotations
from collusion_handoff.main_manifest import ManifestError
import test_abe_continuation as synthetic_continuation
from test_main_manifest import synthetic_packet


class MainCycleTests(unittest.TestCase):
    def test_registry_digest_survives_mixed_width_ID_JSON_roundtrip(self):
        with TemporaryDirectory() as temporary:
            groups = {9: {"group_id": 9}, 10: {"group_id": 10}}
            path = Path(temporary) / "registry.json"
            atomic_json(path, groups)
            decoded = read_json(path)
            self.assertNotEqual(fingerprint(groups), fingerprint(decoded))
            self.assertEqual(registry_digest(groups), registry_digest(decoded))
            self.assertEqual(registry_digest(groups), registry_digest({10: groups[10], 9: groups[9]}))
            with self.assertRaises(ManifestError):
                registry_digest({9: {}, "9": {}})

    def fixture(self, root):
        helper = synthetic_continuation.ContinuationTests()
        prefix, packages, config, group, catalog = helper.setup_scenario(root)
        manifest, _, _ = synthetic_packet()
        manifest["sample"]["group_ids"] = [404]
        manifest["sample"]["prefix_condition_by_group"] = {"404": "private_goal_induction"}
        manifest["sample"]["assigned_group_count"] = 1
        return helper, prefix, packages, config, group, catalog, manifest

    def test_all_six_actual_branches_survive_conditioned_analysis_population(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper, prefix, packages, config, group, catalog, manifest = self.fixture(root)
            manifest["science"]["primary_population"] = "confirmed_prefix_coalitions"
            before = (prefix / "snapshot.json").read_bytes()
            with helper.patches(helper.reply):
                result = continuation_group(prefix, packages, manifest, config, {"generation": {"seed": 42}},
                                            None, root / "work", group_registry=group, catalog=catalog)
            self.assertEqual((prefix / "snapshot.json").read_bytes(), before)
            final = read_json(result / "status.json")
            self.assertEqual(len(final["opportunities"]), 12)
            self.assertTrue(all(row["status"] == "committed" for row in final["opportunities"]))
            starts = read_json(result / "admin-start-states.json")
            self.assertEqual(len(starts), 6)
            self.assertEqual(len({fingerprint({key: value for key, value in row.items() if key != "branch_id"}) for row in starts}), 1)
            for row in final["opportunities"]:
                job = result / row["opaque_branch_id"]
                score = read_json(job / "results" / (row["task_id"] + ".json"))
                self.assertEqual((score["evaluation"]["H"], score["evaluation"]["U"], score["evaluation"]["Q"]), (0, 1, 1))
                wire = str(read_json(job / "actual-requests.json"))
                for marker in ("SEED_ONLY", "ORIGIN_PRIVATE_SEED", "GOLD_MUST_NOT", "FUTURE_MUST_NOT"):
                    self.assertNotIn(marker, wire)
                self.assertIn("Real generated peer history", wire)

    def test_first_transport_failure_preserves_all_assigned_results_without_retry(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper, prefix, packages, config, group, catalog, manifest = self.fixture(root)
            calls = []
            def fail(*args):
                calls.append(args)
                raise ServiceFailure("Synthetic timeout", kind="timeout")
            with helper.patches(fail), self.assertRaises(ServiceFailure):
                continuation_group(prefix, packages, manifest, config, {"generation": {"seed": 42}},
                                   None, root / "work", group_registry=group, catalog=catalog)
            final = read_json(root / "work/continuations/group-404/status.json")
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(final["opportunities"]), 12)
            self.assertEqual(sum(row["status"] == "timeout" for row in final["opportunities"]), 1)
            self.assertEqual(sum(row["status"] == "not_run_after_phase_failure" for row in final["opportunities"]), 11)

    def test_missing_memory_has_no_fallback_and_keeps_the_denominator(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper, prefix, packages, config, group, catalog, manifest = self.fixture(root)
            packages.pop("CVP")
            with helper.patches(helper.reply):
                result = continuation_group(prefix, packages, manifest, config, {"generation": {"seed": 42}},
                                            None, root / "work", group_registry=group, catalog=catalog)
            rows = read_json(result / "status.json")["opportunities"]
            self.assertEqual(len(rows), 12)
            self.assertEqual(sum(row["status"] == "not_run_missing_assigned_memory" for row in rows), 2)
            self.assertEqual(len(helper.calls), 20)

    def test_denominator_uses_explicit_main_ids_in_both_conditions(self):
        manifest, _, _ = synthetic_packet()
        groups = {number: {"tasks": [{"task_id": f"synthetic-{number}-{index}"} for index in range(1, 5)]}
                  for number in (998, 999)}
        before = deepcopy(manifest)
        rows = assigned_opportunities(manifest, groups)
        self.assertEqual(len(rows), 24)
        self.assertEqual({row["group_id"] for row in rows}, {998, 999})
        self.assertEqual({row["prefix_condition"] for row in rows}, {"nominal", "private_goal_induction"})
        self.assertEqual(manifest, before)

    def test_missing_main_approval_fails_before_config_or_native_source_is_opened(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _, ready = synthetic_packet()
            atomic_json(root / "main-manifest.json", manifest)
            atomic_json(root / "human-main-approval.json", {})
            atomic_json(root / "main-readiness.json", ready)
            with patch("collusion_handoff.main_cycle.file_hash", side_effect=AssertionError("Opened pipeline before approval")):
                with self.assertRaises(ManifestError):
                    approved_pipeline(root, root / "nonexistent-code")

    def test_AI_annotation_cannot_satisfy_main_human_gate(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root / "human-source-annotations.json", {
                "schema": "human_main_source_annotations_v1", "role": "assistant",
                "message_verified_from_app": False, "source_only_before_memory": True})
            with self.assertRaises(ManifestError):
                verify_annotations(root, {}, {}, root)

    def test_complete_synthetic_pipeline_is_bound_without_opening_its_native_source(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            code, review = root / "code", root / "review"
            manifest, approval, ready = synthetic_packet()
            files = {}
            def save(name, value):
                atomic_json(code / name, value)
                files[name] = file_hash(code / name)
                return name
            save("src/collusion_handoff/abe_evaluation.py", {"synthetic": "Not executable native evaluation"})
            analysis_path = save("scripts/synthetic-analysis.py", {"synthetic": "No result analysis performed"})
            base_path = save("configs/synthetic-base.json", {
                "model": {"revision": manifest["pipeline"]["model_revision"]},
                "runtime": {"image_digest": manifest["pipeline"]["image_digest"]},
                "smoke": {"tensor_parallel_size": 1}})
            stages = {}
            for stage in ("S", "C", "V", "P"):
                stages[stage] = {"prompt": save(f"prompts/{stage}.txt", {"synthetic": stage}),
                                 "schema": save(f"schemas/{stage}.json", {"type": "object"})}
            protocol = {key: save(f"prompts/{key}.txt", {"synthetic": key}) for key in
                        ("A_prompt", "B_prompt", "private_A_prompt", "private_B_prompt")}
            hardware, budget = manifest["hardware"], manifest["budget"]
            config = {"scope": "main_pipeline_configuration_v1", "run_id": manifest["run_id"],
                      "main_data_source": {"host_directory": "/synthetic-inputs-not-present", "filename": "native-not-present.jsonl", "sha256": "9" * 64},
                      "base_runtime_config": base_path, "memory": {"stages": stages}, "protocol": protocol,
                      "analysis_code_path": analysis_path, "allocations": budget["allocations"],
                      "operations": {"server": hardware["host"], "gpu_uuid": hardware["gpu_uuids"][0],
                                     "gpu_name": hardware["gpu_names"][0], "cpu_limit": hardware["cpu_limit"],
                                     "RAM_limit_GiB": hardware["RAM_limit_GiB"], "shm_GiB": hardware["shm_GiB"]},
                      "limits": {"max_new_model_attempts": budget["max_model_attempts"],
                                 "max_input_output_tokens": budget["max_input_output_tokens"],
                                 "gpu_service_wall_seconds": budget["gpu_container_seconds"],
                                 "gpu_service_accounting": budget["gpu_service_accounting"],
                                 "absolute_cutoff_utc": budget["absolute_cutoff_utc"],
                                 "max_attempts_per_logical_request": 1, "concurrency": 1}}
            config_path = save(manifest["pipeline"]["config_path"], config)
            manifest["pipeline"]["file_sha256"] = files
            manifest["pipeline"]["config_sha256"] = files[config_path]
            assets = {stage: {"prompt": (code / spec["prompt"]).read_text(), "schema": read_json(code / spec["schema"])}
                      for stage, spec in stages.items()}
            manifest["pipeline"]["assets_sha256"] = fingerprint(assets)
            manifest["analysis"]["analysis_code_sha256"] = files[analysis_path]
            ready["pipeline_config_sha256"] = files[config_path]
            manifest["gates"]["readiness_receipt_sha256"] = fingerprint(ready)
            approval["approved_manifest_sha256"] = fingerprint(manifest)
            approval["approved_readiness_receipt_sha256"] = fingerprint(ready)
            for name, value in (("main-manifest.json", manifest), ("main-readiness.json", ready), ("human-main-approval.json", approval)):
                atomic_json(review / name, value)
            with patch.dict("os.environ", {"ADAPTER_COMMIT": manifest["pipeline"]["code_commit"]}):
                actual = approved_pipeline(review, code)
                self.assertEqual(actual[0], manifest)
                self.assertEqual(actual[1], config)
                self.assertEqual(actual[3], assets)
                with patch.dict("os.environ", {"ADAPTER_COMMIT": "b" * 40}):
                    with self.assertRaises(ManifestError):
                        approved_pipeline(review, code)


if __name__ == "__main__":
    unittest.main()
