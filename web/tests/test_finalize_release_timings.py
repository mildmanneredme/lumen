"""Truthful ASR finalization uses tiny local fixtures, never inference or APIs."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web/scripts"))
import book_content as content
import prepare_book_audio as audio
import finalize_release_timings as finalizer
import test_book_content as fixtures


class FinalizeTimingTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ContentContractTests(methodName="runTest")
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.root, self.registry = fixture.production_fixture()
        self.root = self.root.resolve()
        shutil.copytree(fixture.source, self.root / "Draft/v6")
        self.destination = audio.private_destination(self.root, self.root / "Audiobook/author-audit/web-release")
        self.target = ("chapter-001", "charon")
        self.caches = []
        for edition in ["v7", "v8"]:
            job = self.root / "Audiobook" / edition
            manifest = content.read_json(job / "generation-manifest.json")
            delivery = content.read_json(job / "delivery/delivery-manifest.json")
            for item in manifest["items"]:
                track = self.registry["tracks"][item["chapter"]]
                qa_path = job / "mastered" / (track["id"] + ".qa.json")
                qa = content.read_json(qa_path)
                qa.update(assembly={"internal_silence_removed": False, "source_samples": 441000,
                    "leading_source_samples_trimmed": 0, "trailing_source_samples_trimmed": 0,
                    "body_samples": 441000, "added_head_samples": 0, "added_tail_samples": 0},
                    coordinator_checks={**qa["coordinator_checks"], "sample_rate": 44100, "expected_master_samples": 441000,
                        "chunk_timeline": [{"id": item["id"], "source_start_seconds": 0.0, "source_end_seconds": 10.0,
                            "master_start_seconds": 0.0, "master_end_seconds": 10.0, "retained_samples": 441000}]})
                qa_path.write_bytes(content.json_bytes(qa))
                checkpoint_path = qa_path.with_name(track["id"] + ".checkpoint.json")
                checkpoint = content.read_json(checkpoint_path)
                checkpoint["output_sha256"]["qa"] = content.file_hash(qa_path)
                checkpoint_path.write_bytes(content.json_bytes(checkpoint))
                row = delivery["chapters"][item["chapter"]]
                row["source_binding"]["output_sha256"] = checkpoint["output_sha256"]
                row["source_binding"]["checkpoint_sha256"] = content.file_hash(checkpoint_path)
                spoken = content.spoken_comparison(item["text"])
                if edition == "v8" and item["chapter"] == 1:
                    spoken = spoken.replace("Why now?", "")
                words = [{"word": word, "start": .1 + index * .2, "end": .25 + index * .2}
                         for index, word in enumerate(spoken.split())]
                raw = job / "raw" / (item["id"] + ".wav")
                record = {"id": item["id"], "created_at_unix": 1, "expected_inputtext": item["text"], "duration_seconds": 10.0,
                    "cache_identity": {"audio_sha256": content.file_hash(raw), "request_sha256": item["request_sha256"],
                        "inputtext_sha256": hashlib.sha256(item["text"].encode()).hexdigest()}, "segments": [{"words": words}]}
                cache = self.destination / "alignment-cache" / edition / "cache" / (item["id"] + ".fixture.json")
                cache.parent.mkdir(parents=True, exist_ok=True); cache.write_bytes(content.json_bytes(record)); self.caches.append(cache)
            (job / "delivery/delivery-manifest.json").write_bytes(content.json_bytes(delivery))
        self.inventory = content.load_recording_inventory(self.root, self.registry)
        for row in self.inventory["recordings"]:
            row.update(sourceAudioPath=row["sourcePath"], sourceAudioSha256=row["sha256"], bitRate=192000)
        audio.write_pointer(self.destination, "release-inventory.json", self.inventory)
        self.approval = audio.author_approval(self.destination, self.registry, self.inventory["recordings"], initialize=True)
        _, reports = audio.build_timings(self.root, self.destination, self.registry, self.inventory["recordings"], self.approval)
        audio.write_pointer(self.destination, "audio-release-report.json", {"schemaVersion": 1,
            "registrySha256": self.registry["registrySha256"], "recordings": len(reports), "alignmentReports": reports})

    def run_finalize(self):
        with mock.patch("builtins.print"):
            return finalizer.finalize_release(self.root, self.destination, registry=self.registry)

    def test_full_fixture_preserves_canonical_gaps_and_claims_only_technical_alignment(self):
        result = self.run_finalize()
        self.assertEqual(result["recordings"], 8)
        maps = content.read_json(self.destination / "finalized-timings.json")
        target = maps[self.target[0]][self.target[1]]
        gap = next(cue for cue in target["sentences"] if cue["syncStatus"] == "unavailable")
        self.assertIsNone(gap["start"]); self.assertIsNone(gap["end"])
        self.assertEqual(gap["reason"], "no-positive-duration-ASR-anchor")
        self.assertEqual(target["approvals"]["alignment"]["status"], "verified")
        self.assertNotIn("reviewer", target["approvals"]["alignment"])
        self.assertEqual(result["humanAlignmentApproval"], "not-claimed")
        self.assertEqual(result["publicationStatus"], "local-finalized")
        for track in self.registry["tracks"]:
            for narrator, timing in maps[track["id"]].items():
                recording = next(row for row in self.inventory["recordings"] if row["trackId"] == track["id"] and row["narratorId"] == narrator)
                verified, report = finalizer.verify_technical_alignment(self.root, track, recording, timing)
                self.assertTrue(content.validate_timing_map(track, verified, timing, require_approved=True, alignment_verification=report))

    def test_cached_physical_flags_cannot_hide_same_size_changed_source(self):
        recording = self.inventory["recordings"][0]
        raw = self.root / recording["selectedClips"][0]["sourcePath"]
        raw.write_bytes(b"x" * raw.stat().st_size)
        with self.assertRaisesRegex(content.ContentError, "hash"):
            self.run_finalize()
        self.assertFalse((self.destination / "finalized-timings.json").exists())

    def test_missing_or_stale_author_decision_and_report_bindings_fail_closed(self):
        approval_path = self.destination / "author-approval.json"
        original = content.read_json(approval_path)
        changed = copy.deepcopy(original); changed["recordings"][0]["sha256"] = "f" * 64
        approval_path.write_bytes(content.json_bytes(changed))
        with self.assertRaisesRegex(content.ContentError, "approval|decision"):
            self.run_finalize()
        approval_path.write_bytes(content.json_bytes(original))
        report_path = self.destination / "audio-release-report.json"
        report = content.read_json(report_path); report["registrySha256"] = "f" * 64
        report_path.write_bytes(content.json_bytes(report))
        with self.assertRaisesRegex(content.ContentError, "report|registry"):
            self.run_finalize()

    def test_cache_change_after_finalization_invalidates_actual_technical_proof(self):
        self.run_finalize(); maps = content.read_json(self.destination / "finalized-timings.json")
        timing = maps[self.target[0]][self.target[1]]
        cache = self.root / timing["evidence"][0]["sourcePath"]
        cache.write_bytes(cache.read_bytes().replace(b'"created_at_unix":1', b'"created_at_unix":2'))
        recording = next(row for row in self.inventory["recordings"] if (row["trackId"], row["narratorId"]) == self.target)
        with self.assertRaisesRegex(content.ContentError, "cache|Cache"):
            finalizer.verify_technical_alignment(self.root, self.registry["tracks"][1], recording, timing)

    def test_same_hash_flags_cannot_approve_forged_recomputed_map(self):
        self.run_finalize(); timing = content.read_json(self.destination / "finalized-timings.json")[self.target[0]][self.target[1]]
        changed = copy.deepcopy(timing); changed["sentences"][0]["start"] += .01
        changed["coverage"]["introEnd"] = changed["sentences"][0]["start"]
        approval = changed["approvals"]["alignment"]
        report = content.read_project_json(self.root, approval["verificationReportPath"])
        report["timingSha256"] = content.timing_content_hash(changed)
        artifact = audio.private_export(self.destination, ("technical-alignment",), self.target[0], "forged", report)
        approval.update(timingSha256=report["timingSha256"], verificationReportSha256=artifact["sha256"],
                        verificationReportPath=(self.destination / artifact["path"]).relative_to(self.root).as_posix())
        recording = next(row for row in self.inventory["recordings"] if (row["trackId"], row["narratorId"]) == self.target)
        with self.assertRaisesRegex(content.ContentError, "recomputed|measured"):
            finalizer.verify_technical_alignment(self.root, self.registry["tracks"][1], recording, changed)

    def test_external_cache_symlink_is_rejected(self):
        path = self.caches[0]; outside = self.root.parent / "outside-cache.json"
        outside.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(outside)
        with self.assertRaises(content.ContentError):
            self.run_finalize()

    def test_exporter_rechecks_technical_report_and_preserves_public_null_gaps(self):
        import release_export
        self.run_finalize(); maps = content.read_json(self.destination / "finalized-timings.json")
        plan = {"schemaVersion": 1, "releaseId": "fixture-as-is", "accessModel": "private", "author": "Fixture author",
                "appDataURLbase": "https://reader.example/api/assets/", "mediaURLbase": "https://reader.example/api/assets/",
                "narrators": [{"id": "charon", "label": "Charon"}], "defaultTrackId": self.target[0], "defaultNarratorId": "charon",
                "tracks": [{"id": self.target[0], "requiredNarratorIds": ["charon"]}]}
        result = release_export.build_release(self.root, self.registry, self.inventory, maps, {}, plan, self.root / "private-export")
        entry = next(row for row in result["uploadInventory"]["assets"] if row["url"] == result["manifest"]["tracks"][0]["recordings"]["charon"]["url"])
        public = content.read_json(entry["sourcePath"])
        gap = next(sentence for paragraph in public["paragraphs"] for sentence in paragraph["sentences"] if sentence["syncStatus"] == "unavailable")
        self.assertIsNone(gap["start"]); self.assertIsNone(gap["end"])
        self.assertNotIn("evidenceIds", gap); self.assertNotIn("recheckWindow", gap)
        cache = self.root / maps[self.target[0]][self.target[1]]["evidence"][0]["sourcePath"]
        cache.write_bytes(cache.read_bytes() + b" ")
        with self.assertRaisesRegex(content.ContentError, "cache"):
            release_export.build_release(self.root, self.registry, self.inventory, maps, {}, plan, self.root / "blocked-export")
        self.assertFalse((self.root / "blocked-export").exists())

    def test_all_gap_recording_uses_null_cues_and_diagnostic_whole_recording_window(self):
        recording = self.inventory["recordings"][0]
        evidence = [{"id": clip["id"]} for clip in recording["selectedClips"]]
        track = self.registry["tracks"][0]
        report = finalizer.report_from_words(track, recording, [("unrelated", 1, 2)], evidence, {})
        timing = finalizer.gap_timing(track, recording, report, {})
        self.assertTrue(all(cue["syncStatus"] == "unavailable" and cue["start"] is None and cue["end"] is None for cue in timing["sentences"]))
        self.assertEqual(timing["coverage"], {"introEnd": 0, "tailStart": recording["decodedDuration"]})
        self.assertTrue(all(cue["recheckWindow"]["scope"] == "neighbor-recheck-only" for cue in timing["sentences"]))

    def test_technical_report_false_check_or_selection_hash_is_rejected(self):
        self.run_finalize(); timing = content.read_json(self.destination / "finalized-timings.json")[self.target[0]][self.target[1]]
        original = content.read_project_json(self.root, timing["approvals"]["alignment"]["verificationReportPath"])
        recording = next(row for row in self.inventory["recordings"] if (row["trackId"], row["narratorId"]) == self.target)
        for mutation in [lambda report: report["checks"].update(cacheBytesVerified=False),
                         lambda report: report.update(selectionSha256="f" * 64),
                         lambda report: report.update(humanAlignmentApproval="approved")]:
            report = copy.deepcopy(original); mutation(report)
            changed = copy.deepcopy(timing); changed["approvals"]["alignment"]["verificationReportSha256"] = content.object_hash(report)
            with self.assertRaises(content.ContentError):
                content.validate_technical_alignment(self.registry["tracks"][1], recording, changed, report)

    def test_dry_run_rebuilds_without_artifact_writes_or_bulk_physical_reads(self):
        before = {path.relative_to(self.root).as_posix(): content.file_hash(path)
                  for path in self.root.rglob("*") if path.is_file()}
        with mock.patch.object(content, "verify_recording_file", side_effect=AssertionError("bulk MP3 read forbidden")), \
             mock.patch.object(content, "verify_recording_sources", side_effect=AssertionError("bulk raw read forbidden")):
            report = finalizer.dry_run(self.root, self.destination, registry=self.registry)
        after = {path.relative_to(self.root).as_posix(): content.file_hash(path)
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(report["artifactsWritten"]); self.assertFalse(report["physicalSourcesVerified"])
        self.assertEqual(report["recordings"], 8); self.assertEqual(report["unavailableSentences"], 1)

    def test_overlapping_measured_sentence_prefers_gap_and_rebounds_after_filtering(self):
        track = self.registry["tracks"][1]; recording = self.inventory["recordings"][1]
        sentences = content.sentences(track)
        cues = [{"sentenceId": sentence["id"], "start": index + 1, "end": index + 1.8} for index, sentence in enumerate(sentences)]
        cues[1]["start"] = 1.5
        evidence = [{"id": clip["id"]} for clip in recording["selectedClips"]]
        report = {"method": "fixture", "measuredSentences": cues, "evidence": evidence, "sourceBindings": {},
                  "unanchoredSentences": [], "overlappingSentences": [sentences[1]["id"]], "syncGaps": [
                     {"sentenceId": sentences[1]["id"], "reason": "overlapping-ASR-interval", "evidenceIds": [evidence[0]["id"]]}]}
        timing = finalizer.gap_timing(track, recording, report, {})
        gap = timing["sentences"][1]
        self.assertEqual(gap["syncStatus"], "unavailable"); self.assertIsNone(gap["start"])
        self.assertEqual(gap["recheckWindow"]["start"], timing["sentences"][0]["end"])
        self.assertEqual(gap["recheckWindow"]["end"], timing["sentences"][2]["start"])


if __name__ == "__main__":
    unittest.main()
