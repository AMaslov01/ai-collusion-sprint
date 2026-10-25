"""Delegated collection keeps independent AI source labels distinct from human gold."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from collusion_handoff.artifacts import atomic_json, fingerprint
from collusion_handoff.main_cycle import (annotation_receipt_name, authority_receipt_name, file_hash,
    registry_digest, verify_annotations)
from collusion_handoff.main_manifest import ManifestError


class MainAnnotationTests(unittest.TestCase):
    def packet(self, root):
        manifest = {"gates": {"authorization_mode": "delegated_autonomous_after_diagnostics",
                    "source_annotation_timing": "independent_AI_source_first_human_audit_before_claims"}}
        groups = {404: {"tasks": [{"memory": None}, {"memory": None}, {"memory": "synthetic-source-Q3"}, {"memory": "synthetic-source-Q4"}]}}
        source = {"archive": {"events": [
            {"event_id": "generated-A", "event_type": "assistant_action", "role": "A", "content": "Synthetic future arrangement"},
            {"event_id": "generated-B", "event_type": "peer_message", "role": "B", "content": "Synthetic generated acceptance"}]}}
        source_path = root / "prefixes/prefix/group-404/source.json"
        atomic_json(source_path, source)
        reference = root / "source-reference.json"
        atomic_json(reference, {"source_file_sha256": file_hash(source_path), "memory_outputs_opened": False})
        row = {"source_file_sha256": file_hash(source_path), "Q_mapping_sha256": fingerprint([task["memory"] for task in groups[404]["tasks"][2:]]),
               "formation_review_complete": True, "whole_source_reviewed": True, "reference_file": reference.name,
               "reference_sha256": file_hash(reference), "formation_status": "uncertain", "checkpoint_status": "uncertain",
               "evidence": [{"event_id": event["event_id"], "start": 0, "end": len(event["content"].encode()), "quote": event["content"]}
                            for event in source["archive"]["events"]]}
        receipt = {"schema": "independent_AI_main_source_annotations_v1", "role": "assistant", "human_gold": False,
                   "source_only_before_memory": True, "manifest_sha256": fingerprint(manifest), "registry_sha256": registry_digest(groups),
                   "groups": {"404": {**row, "harmful_future_agreement_present": None}}, "reviewers": []}
        for reviewer, status, checkpoint in (("synthetic-one", "confirmed", "active"), ("synthetic-two", "uncertain", "uncertain")):
            file = root / (reviewer + ".json")
            review = {"schema": "independent_AI_main_source_review_v1", "role": "assistant", "reviewer_id": reviewer,
                      "human_gold": False, "source_only_before_memory": True, "independently_locked": True,
                      "manifest_sha256": fingerprint(manifest), "registry_sha256": registry_digest(groups),
                      "groups": {"404": {**row, "formation_status": status, "checkpoint_status": checkpoint}}}
            atomic_json(file, review)
            receipt["reviewers"].append({"reviewer_id": reviewer, "review_file": file.name, "review_sha256": file_hash(file)})
        atomic_json(root / "ai-source-annotations.json", receipt)
        return manifest, groups, receipt

    def test_truthful_file_selection_and_uncertain_collection_without_human_gold(self):
        self.assertEqual(authority_receipt_name({}), "human-main-approval.json")
        self.assertEqual(annotation_receipt_name({}), "human-source-annotations.json")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, groups, receipt = self.packet(root)
            self.assertEqual(authority_receipt_name(manifest), "delegated-main-execution.json")
            actual = verify_annotations(root, manifest, groups, root / "prefixes")
            self.assertEqual(actual, receipt)
            self.assertIsNone(actual["groups"]["404"]["harmful_future_agreement_present"])
            self.assertFalse(actual["human_gold"])

    def test_single_review_changed_lock_false_gold_and_disagreement_cannot_become_confirmed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, groups, original = self.packet(root)
            changes = [lambda row: row.update(human_gold=True),
                       lambda row: row.update(source_only_before_memory=False),
                       lambda row: row.update(reviewers=row["reviewers"][:1]),
                       lambda row: row["groups"]["404"].update(formation_status="confirmed", checkpoint_status="active", harmful_future_agreement_present=True),
                       lambda row: row["groups"]["404"].update(Q_mapping_sha256="0" * 64)]
            for change in changes:
                value = deepcopy(original)
                change(value)
                atomic_json(root / "ai-source-annotations.json", value)
                with self.assertRaises(ManifestError):
                    verify_annotations(root, manifest, groups, root / "prefixes")
            atomic_json(root / "ai-source-annotations.json", original)
            atomic_json(root / original["reviewers"][0]["review_file"], {"altered_after_lock": True})
            with self.assertRaises(ManifestError):
                verify_annotations(root, manifest, groups, root / "prefixes")


if __name__ == "__main__":
    unittest.main()
