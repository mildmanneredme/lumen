"""Publishable payloads require current sources and explicit reviewed clocks."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web/scripts"))
import release_export as release
import test_book_content as fixtures


class ReleaseExportTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ContentContractTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.registry = fixture.production_fixture()
        self.root = self.root.resolve()
        shutil.copytree(fixture.source, self.root / "Draft/v6")
        self.track = self.registry["tracks"][1]
        paragraph = self.track["paragraphs"][0]
        sentence = paragraph["sentences"][0]
        self.registry["legacyAliases"] = {"chapter-001-pilot": {
            "trackId": self.track["id"], "paragraphIds": {"p001": paragraph["id"]},
            "sentenceIds": {"p001-s01": sentence["id"]},
            "completedExcerpt": {"sentenceId": sentence["id"], "sentenceFraction": 1.0, "trackCompleted": False}}}
        self.registry["registrySha256"] = release.content.object_hash(
            {key: value for key, value in self.registry.items() if key != "registrySha256"})
        self.inventory = release.content.load_recording_inventory(self.root, self.registry)
        self.recording = next(row for row in self.inventory["recordings"]
                              if row["trackId"] == self.track["id"] and row["narratorId"] == "charon")
        cues = [{"sentenceId": sentence["id"], "start": index + 1.0, "end": index + 1.5}
                for index, sentence in enumerate(release.content.sentences(self.track))]
        self.timing = {"schemaVersion": 1, "trackId": self.track["id"], "manuscriptVersion": "v6",
                       "textSha256": self.track["textSha256"], "narratorId": "charon",
                       "audioSha256": self.recording["sha256"], "duration": self.recording["decodedDuration"],
                       "sentences": cues, "coverage": {"introEnd": cues[0]["start"], "tailStart": cues[-1]["end"]},
                       "approvals": {}}
        approval = {"status": "approved", "reviewer": "Fixture reviewer", "reviewedAt": "2026-10-10T12:00:00Z",
                    "audioSha256": self.recording["sha256"], "textSha256": self.track["textSha256"]}
        self.timing["approvals"] = {"content": dict(approval), "alignment": dict(approval)}
        self.timing["approvals"]["alignment"]["timingSha256"] = release.content.timing_content_hash(self.timing)
        self.timings = {self.track["id"]: {"charon": self.timing}}
        self.plan = {"schemaVersion": 1, "releaseId": "fixture-beta-1", "accessModel": "private",
                     "appDataURLbase": "https://reader.example/api/assets/fixture/", "mediaURLbase": "https://reader.example/api/assets/fixture/",
                     "author": "Rob Xie", "defaultTrackId": self.track["id"], "defaultNarratorId": "charon",
                     "narrators": [{"id": "autonoe", "label": "Autonoe"}, {"id": "charon", "label": "Charon"}],
                     "tracks": [{"id": self.track["id"], "extentId": "chapter-001-full", "requiredNarratorIds": ["charon"]}]}
        self.staging = self.root / "private-release"

    def build(self, **changes):
        values = dict(root=self.root, registry=self.registry, inventory=self.inventory,
                      timing_maps=self.timings, scenes_by_track={}, plan=self.plan, staging=self.staging)
        values.update(changes)
        return release.build_release(**values)

    def read_chapter(self, result):
        entry = next(row for row in result["uploadInventory"]["assets"]
                     if row["url"] == result["manifest"]["tracks"][0]["recordings"]["charon"]["url"])
        return json.loads(Path(entry["sourcePath"]).read_text())

    def test_subset_exports_faithful_ready_voice_and_honest_pending_voice(self):
        result = self.build()
        manifest = result["manifest"]
        self.assertEqual([row["id"] for row in manifest["tracks"]], ["chapter-001"])
        self.assertEqual(manifest["tracks"][0]["recordings"]["charon"]["status"], "ready")
        self.assertEqual(manifest["tracks"][0]["recordings"]["autonoe"], {"status": "pending"})
        self.assertEqual(manifest["legacyAliases"], self.registry["legacyAliases"])
        chapter = self.read_chapter(result)
        self.assertEqual(chapter["chapterId"], self.track["id"])
        self.assertEqual(chapter["id"], "chapter-001-full")
        self.assertEqual(chapter["readingExtent"], "full")
        self.assertEqual(chapter["readingExtentId"], chapter["id"])
        self.assertEqual(chapter["audio"]["decodedDuration"], self.recording["decodedDuration"])
        self.assertEqual(chapter["scenes"], [])
        self.assertEqual(chapter["blocks"], self.track["blocks"])
        for expected, actual in zip(self.track["paragraphs"], chapter["paragraphs"]):
            for key in ["id", "markdown", "text", "emphasis"]:
                self.assertEqual(actual[key], expected[key])
            self.assertEqual(actual["start"], actual["sentences"][0]["start"])
            self.assertEqual(actual["end"], actual["sentences"][-1]["end"])
            for sentence, canonical in zip(actual["sentences"], expected["sentences"]):
                self.assertEqual({key: sentence[key] for key in canonical}, canonical)

    def test_private_metadata_and_cast_fields_never_enter_public_payloads(self):
        self.track["privateCastNotes"] = "SECRET CAST"
        self.track["paragraphs"][0]["sentences"][0]["privateCastNotes"] = "SECRET SENTENCE CAST"
        self.recording["reviewerNotes"] = "SECRET REVIEW"
        # These extra annotations are private; the canonical digest has no private fields.
        self.track["textSha256"] = release.content.object_hash({key: self.track[key]
            for key in ["id", "manuscriptVersion", "bodyMarkdown", "blocks", "paragraphs"]})
        self.timing["textSha256"] = self.track["textSha256"]
        for approval in self.timing["approvals"].values():
            approval["textSha256"] = self.track["textSha256"]
        self.timing["approvals"]["alignment"]["timingSha256"] = release.content.timing_content_hash(self.timing)
        self.recording["textSha256"] = self.track["textSha256"]
        self.inventory["recordings"][1]["textSha256"] = self.track["textSha256"]
        self.registry["registrySha256"] = release.content.object_hash(
            {key: value for key, value in self.registry.items() if key != "registrySha256"})
        self.inventory["registrySha256"] = self.registry["registrySha256"]
        result = self.build()
        public = json.dumps([result["manifest"], self.read_chapter(result)])
        for token in ["SECRET", "sourcePath", "selectedClips", "approvals", "generationManifestSha256", "reviewer"]:
            self.assertNotIn(token, public)

    def test_required_recording_or_reviewed_timing_missing_rejects_before_writes(self):
        for timings in [{}, {self.track["id"]: {}}]:
            with self.subTest(timings=timings):
                with self.assertRaisesRegex(release.content.ContentError, "required.*timing"):
                    self.build(timing_maps=timings)
                self.assertFalse(self.staging.exists())
        plan = copy.deepcopy(self.plan)
        plan["narrators"].append({"id": "future", "label": "Future narrator"})
        plan["tracks"][0]["requiredNarratorIds"].append("future")
        with self.assertRaisesRegex(release.content.ContentError, "required recording"):
            self.build(plan=plan)
        self.assertFalse(self.staging.exists())

    def test_full_export_cannot_reuse_legacy_pilot_extent(self):
        plan = copy.deepcopy(self.plan)
        plan["tracks"][0]["extentId"] = "chapter-001-pilot"
        with self.assertRaisesRegex(release.content.ContentError, "extent"):
            self.build(plan=plan)

    def test_legacy_aliases_reject_private_extra_fields(self):
        self.registry["legacyAliases"]["chapter-001-pilot"]["privateCastNotes"] = "SECRET CAST"
        self.registry["registrySha256"] = release.content.object_hash(
            {key: value for key, value in self.registry.items() if key != "registrySha256"})
        self.inventory["registrySha256"] = self.registry["registrySha256"]
        with self.assertRaisesRegex(release.content.ContentError, "alias"):
            self.build()
        self.assertFalse(self.staging.exists())

    def test_pending_human_or_mutated_alignment_review_cannot_publish(self):
        for mutation in ["pending", "cue"]:
            timing = copy.deepcopy(self.timing)
            if mutation == "pending":
                timing["approvals"]["content"]["status"] = "pending"
            else:
                timing["sentences"][0]["end"] += .1
            with self.subTest(mutation=mutation):
                with self.assertRaises(release.content.ContentError):
                    self.build(timing_maps={self.track["id"]: {"charon": timing}})
                self.assertFalse(self.staging.exists())

    def test_export_rechecks_same_size_master_and_raw_tampering(self):
        for field in ["master", "raw"]:
            path = self.root / (self.recording["sourcePath"] if field == "master"
                                else self.recording["selectedClips"][0]["sourcePath"])
            original = path.read_bytes()
            path.write_bytes(b"x" * len(original))
            with self.subTest(field=field):
                with self.assertRaisesRegex(release.content.ContentError, "hash"):
                    self.build()
                self.assertFalse(self.staging.exists())
            path.write_bytes(original)

    def test_stale_inventory_and_changed_manuscript_reject(self):
        changed = copy.deepcopy(self.inventory)
        changed["recordings"][0]["masterIdentitySha256"] = "a" * 64
        with self.assertRaisesRegex(release.content.ContentError, "inventory"):
            self.build(inventory=changed)
        source = self.root / "Draft/v6/part1.md"
        source.write_text(source.read_text() + "Changed")
        with self.assertRaisesRegex(release.content.ContentError, "source"):
            self.build()
        self.assertFalse(self.staging.exists())

    def test_rehashed_registry_cannot_rewrite_prose_under_original_source_hash(self):
        registry = copy.deepcopy(self.registry)
        original = registry["tracks"][1]
        rewritten = release.content.make_track(1, "story", original["bodyMarkdown"].replace("waited", "stayed"), "v6", 1)
        rewritten["narrationSha256"] = original["narrationSha256"]
        registry["tracks"][1] = rewritten
        registry["registrySha256"] = release.content.object_hash({key: value for key, value in registry.items()
                                                               if key != "registrySha256"})
        inventory = release.content.load_recording_inventory(self.root, registry)
        timing = copy.deepcopy(self.timing)
        timing["textSha256"] = rewritten["textSha256"]
        for approval in timing["approvals"].values():
            approval["textSha256"] = rewritten["textSha256"]
        timing["approvals"]["alignment"]["timingSha256"] = release.content.timing_content_hash(timing)
        with self.assertRaisesRegex(release.content.ContentError, "(?i)registry.*source|source.*registry"):
            self.build(registry=registry, inventory=inventory, timing_maps={self.track["id"]: {"charon": timing}})
        self.assertFalse(self.staging.exists())

    def test_required_default_and_selection_are_explicit(self):
        changes = [{"accessModel": None}, {"defaultTrackId": "chapter-002"}, {"defaultNarratorId": "autonoe"},
                   {"tracks": []}, {"tracks": [self.plan["tracks"][0], self.plan["tracks"][0]]}]
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(release.content.ContentError):
                    self.build(plan=dict(self.plan, **change))
                self.assertFalse(self.staging.exists())

    def test_private_plans_reject_origins_and_paths_the_uploader_cannot_serve(self):
        for access in ["private", "authenticated"]:
            for changes in [
                {"appDataURLbase": "https://reader.example/releases/", "mediaURLbase": "https://reader.example/api/assets/"},
                {"appDataURLbase": "https://reader.example/api/assets/", "mediaURLbase": "https://media.example/api/assets/"},
                {"appDataURLbase": "https://reader.example/api/assets/", "mediaURLbase": "https://reader.example/audio/"},
                {"appDataURLbase": "https://reader.example/api/assets/release:beta/", "mediaURLbase": "https://reader.example/api/assets/"},
                {"appDataURLbase": "https://reader.example/api/assets/", "mediaURLbase": "https://reader.example/api/assets//beta/"},
                {"appDataURLbase": "https://münich.example/api/assets/", "mediaURLbase": "https://münich.example/api/assets/"},
                {"appDataURLbase": "https://reader.example/api/assets/" + "x" * 490 + "/", "mediaURLbase": "https://reader.example/api/assets/"},
            ]:
                with self.subTest(access=access, changes=changes):
                    with self.assertRaises(release.content.ContentError):
                        self.build(plan=dict(self.plan, accessModel=access, **changes))
                    self.assertFalse(self.staging.exists())

    def test_https_default_port_normalizes_for_private_delivery(self):
        plan = dict(self.plan, appDataURLbase="https://READER.example:443/api/assets/",
                    mediaURLbase="https://reader.example:00443/api/assets/")
        result = self.build(plan=plan)
        self.assertEqual(result["uploadInventory"]["appOrigin"], "https://reader.example")
        self.assertEqual(result["uploadInventory"]["mediaOrigins"],
                         ["https://reader.example"])
        self.assertTrue(all(":443" not in row["url"] and ":00443" not in row["url"]
                            for row in result["uploadInventory"]["assets"]))
        self.assertEqual(release.url_base("https://[::1]:443/assets/", "fixture"),
                         ("https://[::1]/assets/", "https://[::1]"))
        self.assertEqual(release.url_base("https://[0:0:0:0:0:0:0:1]:00443/assets/", "fixture"),
                         ("https://[::1]/assets/", "https://[::1]"))
        self.assertEqual(release.url_base("https://reader.example:8443/assets/", "fixture"),
                         ("https://reader.example:8443/assets/", "https://reader.example:8443"))

    def test_authenticated_api_and_public_cross_origin_plans_remain_supported(self):
        private = self.build(plan=dict(self.plan, accessModel="authenticated"))
        self.assertEqual(private["uploadInventory"]["mediaOrigins"], ["https://reader.example"])
        public = self.build(plan=dict(self.plan, accessModel="public",
            appDataURLbase="https://reader.example/releases/", mediaURLbase="https://media.example/lumen/"))
        self.assertEqual(public["uploadInventory"]["mediaOrigins"],
                         ["https://reader.example", "https://media.example"])
        self.assertTrue(self.read_chapter(public)["audio"]["src"].startswith("https://media.example/lumen/"))

    def test_url_bases_require_absolute_secure_stable_urls(self):
        for url in ["/media/", "http://media.example/", "https://user:secret@media.example/",
                    "https://media.example/?token=secret", "https://media.example/#fragment", "https://media.example/a/../b/"]:
            with self.subTest(url=url):
                with self.assertRaises(release.content.ContentError):
                    self.build(plan=dict(self.plan, mediaURLbase=url))
                self.assertFalse(self.staging.exists())

    def test_upload_inventory_matches_exact_local_bytes_and_three_range_windows(self):
        result = self.build()
        upload = result["uploadInventory"]
        self.assertEqual(upload["appOrigin"], "https://reader.example")
        self.assertEqual(upload["mediaOrigins"], ["https://reader.example"])
        for row in upload["assets"]:
            data = Path(row["sourcePath"]).read_bytes()
            self.assertTrue(row["immutable"])
            self.assertEqual(row["bytes"], len(data))
            self.assertEqual(row["sha256"], release.hash_bytes(data))
            width = min(1024, len(data))
            starts = [0, (len(data) - width) // 2, len(data) - width]
            self.assertEqual([sample["start"] for sample in row["samples"]], starts)
            for sample in row["samples"]:
                self.assertLessEqual(sample["end"] - sample["start"] + 1, 1024)
                self.assertEqual(sample["sha256"], release.hash_bytes(data[sample["start"]:sample["end"] + 1]))

    def test_exports_are_content_addressed_idempotent_and_collision_safe(self):
        first = self.build()
        paths = [Path(row["sourcePath"]) for row in first["uploadInventory"]["assets"]
                 if self.staging in Path(row["sourcePath"]).parents]
        times = {path: path.stat().st_mtime_ns for path in paths}
        second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(times, {path: path.stat().st_mtime_ns for path in paths})
        paths[0].write_bytes(b"corrupted")
        with self.assertRaisesRegex(release.content.ContentError, "immutable"):
            self.build()

    def test_staging_never_targets_public_runtime_or_child_symlink_escape(self):
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        for destination in [public, public / "assets"]:
            with self.assertRaisesRegex(release.content.ContentError, "private staging"):
                self.build(staging=destination)
        self.staging.mkdir()
        (self.staging / "tracks").symlink_to(public, target_is_directory=True)
        with self.assertRaises(release.content.ContentError):
            self.build()
        self.assertEqual(list(public.iterdir()), [])

    def test_reviewed_scene_resolves_anchor_and_rejects_unsafe_or_unapproved_visible_text(self):
        image = self.root / "approved-art/scene.webp"
        image.parent.mkdir()
        image.write_bytes(b"fixture approved painting")
        scene = {"id": "fixture-scene", "sentenceId": self.timing["sentences"][0]["sentenceId"],
                 "sourcePath": "approved-art/scene.webp", "sha256": release.content.file_hash(image),
                 "title": "A quiet room", "alt": "A person waiting in a room"}
        scene["approval"] = {"status": "approved", "reviewer": "Fixture art reviewer",
                             "reviewedAt": "2026-10-10T12:00:00Z", "sceneSha256": release.scene_content_hash(scene)}
        result = self.build(scenes_by_track={self.track["id"]: [scene]})
        actual = self.read_chapter(result)["scenes"][0]
        self.assertEqual(actual["start"], self.timing["sentences"][0]["start"])
        self.assertEqual(actual["description"], scene["alt"])
        self.assertNotIn("sourcePath", actual)
        for change in [{"alt": "<script>spoiler</script>"}, {"title": "Changed reveal"}, {"approval": {}}]:
            with self.subTest(change=change):
                with self.assertRaises(release.content.ContentError):
                    self.build(scenes_by_track={self.track["id"]: [dict(scene, **change)]})

    def test_schema2_public_payload_keeps_unavailable_sentence_text_without_private_windows(self):
        timing = copy.deepcopy(self.timing); timing["schemaVersion"] = 2
        for cue in timing["sentences"]:
            cue["syncStatus"] = "measured"
        gap = timing["sentences"][1]
        gap.update(syncStatus="unavailable", start=None, end=None, reason="no-positive-duration-ASR-anchor",
                   evidenceIds=[self.recording["selectedClips"][0]["id"]],
                   recheckWindow={"start": timing["sentences"][0]["end"], "end": timing["sentences"][2]["start"],
                                  "beforeSentenceId": timing["sentences"][0]["sentenceId"],
                                  "afterSentenceId": timing["sentences"][2]["sentenceId"], "scope": "neighbor-recheck-only"})
        timing["approvals"]["alignment"]["timingSha256"] = release.content.timing_content_hash(timing)
        result = self.build(timing_maps={self.track["id"]: {"charon": timing}})
        chapter = self.read_chapter(result)
        self.assertEqual(chapter["schemaVersion"], 2)
        sentences = [row for paragraph in chapter["paragraphs"] for row in paragraph["sentences"]]
        self.assertEqual(sentences[1]["text"], release.content.sentences(self.track)[1]["text"])
        self.assertEqual(sentences[1]["syncStatus"], "unavailable")
        self.assertIsNone(sentences[1]["start"]); self.assertIsNone(sentences[1]["end"])
        self.assertNotIn("recheckWindow", sentences[1]); self.assertNotIn("evidenceIds", sentences[1])
        self.assertEqual(chapter["paragraphs"][0]["end"], timing["sentences"][0]["end"])

    def test_schema2_paragraph_bounds_use_measured_sentences_or_explicit_nulls(self):
        timing = copy.deepcopy(self.timing); timing["schemaVersion"] = 2
        for cue in timing["sentences"]:
            cue.update(syncStatus="unavailable", start=None, end=None, reason="no-positive-duration-ASR-anchor",
                       evidenceIds=[self.recording["selectedClips"][0]["id"]],
                       recheckWindow={"start": 0, "end": timing["duration"], "beforeSentenceId": None,
                                      "afterSentenceId": None, "scope": "neighbor-recheck-only"})
        timing["coverage"] = {"introEnd": 0, "tailStart": timing["duration"]}
        timing["approvals"]["alignment"]["timingSha256"] = release.content.timing_content_hash(timing)
        chapter = self.read_chapter(self.build(timing_maps={self.track["id"]: {"charon": timing}}))
        self.assertTrue(all(paragraph["start"] is None and paragraph["end"] is None for paragraph in chapter["paragraphs"]))

    def test_technical_alignment_claim_with_no_actual_private_report_fails_before_staging(self):
        timing = copy.deepcopy(self.timing); timing["schemaVersion"] = 2
        for cue in timing["sentences"]:
            cue["syncStatus"] = "measured"
        timing["approvals"]["alignment"] = {"status": "verified", "timingSha256": release.content.timing_content_hash(timing)}
        with self.assertRaisesRegex(release.content.ContentError, "technical|verification"):
            self.build(timing_maps={self.track["id"]: {"charon": timing}})
        self.assertFalse(self.staging.exists())

    def test_missing_optional_recording_is_unavailable_without_asset_url(self):
        # Canonical production manifests stay complete; plan narrators can describe a future edition.
        plan = copy.deepcopy(self.plan)
        plan["narrators"].append({"id": "future", "label": "Future narrator"})
        result = self.build(plan=plan)
        self.assertEqual(result["manifest"]["tracks"][0]["recordings"]["future"], {"status": "unavailable"})

    def test_scene_export_requires_optimized_webp(self):
        for extension in ["png", "jpg", "jpeg"]:
            image = self.root / ("approved-art/scene." + extension)
            image.parent.mkdir(exist_ok=True)
            image.write_bytes(b"fixture legacy painting")
            scene = {"id": "fixture-scene", "sentenceId": self.timing["sentences"][0]["sentenceId"],
                     "sourcePath": str(image.relative_to(self.root)), "sha256": release.content.file_hash(image),
                     "title": "A quiet room", "alt": "A person waiting in a room"}
            scene["approval"] = {"status": "approved", "reviewer": "Fixture art reviewer",
                                 "reviewedAt": "2026-10-10T12:00:00Z", "sceneSha256": release.scene_content_hash(scene)}
            with self.subTest(extension=extension):
                with self.assertRaisesRegex(release.content.ContentError, "WebP"):
                    self.build(scenes_by_track={self.track["id"]: [scene]})

    def test_narrator_clocks_remain_independent(self):
        female = next(row for row in self.inventory["recordings"]
                      if row["trackId"] == self.track["id"] and row["narratorId"] == "autonoe")
        timing = copy.deepcopy(self.timing)
        timing.update(narratorId="autonoe", audioSha256=female["sha256"])
        for cue in timing["sentences"]:
            cue["start"] += .25; cue["end"] += .25
        timing["coverage"] = {"introEnd": timing["sentences"][0]["start"], "tailStart": timing["sentences"][-1]["end"]}
        for approval in timing["approvals"].values():
            approval["audioSha256"] = female["sha256"]
        timing["approvals"]["alignment"]["timingSha256"] = release.content.timing_content_hash(timing)
        result = self.build(timing_maps={self.track["id"]: {"charon": self.timing, "autonoe": timing}})
        female_url = result["manifest"]["tracks"][0]["recordings"]["autonoe"]["url"]
        entry = next(row for row in result["uploadInventory"]["assets"] if row["url"] == female_url)
        chapter = json.loads(Path(entry["sourcePath"]).read_text())
        self.assertEqual(chapter["paragraphs"][0]["start"], 1.25)
        self.assertEqual(self.read_chapter(result)["paragraphs"][0]["start"], 1.0)


if __name__ == "__main__":
    unittest.main()
