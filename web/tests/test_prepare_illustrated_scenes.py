"""Generated selections cannot bypass source, canonical-clock, or review gates."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web/scripts"))
import prepare_illustrated_scenes as images
import test_book_content as fixtures


class IllustratedScenePreparationTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ContentContractTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = Path(fixture.temp.name).resolve()
        self.registry = fixture.registry()
        self.track = self.registry["tracks"][1]
        self.directory = self.root / "asset-staging/selections"
        self.directory.mkdir(parents=True)
        self.manifest_path = self.directory / (self.track["id"] + "-selected-scenes.json")
        self.selection = {"schemaVersion": 1, "bookId": "lumen", "chapterId": self.track["id"],
                          "manuscriptVersion": "v6", "sharedAcrossNarrators": True,
                          "narratorTimestamps": None, "scenes": []}
        for index, sentence in enumerate(images.content.sentences(self.track)[:2], 1):
            full, phone = self.directory / f"painting-{index}.webp", self.directory / f"painting-{index}-phone.webp"
            full.write_bytes(f"fixture full painting {index}".encode())
            phone.write_bytes(f"fixture phone painting {index}".encode())
            self.selection["scenes"].append({
                "id": f"painting-{index}", "trackId": self.track["id"], "sentenceId": sentence["id"],
                "sentenceTextSha256": hashlib.sha256(sentence["text"].encode()).hexdigest(),
                "title": "Production prompt reveals a future character",
                "alt": "Editorial generation notes containing future events",
                "sourcePath": full.relative_to(self.root).as_posix(), "sha256": images.content.file_hash(full),
                "phone": {"path": phone.relative_to(self.root).as_posix(), "sha256": images.content.file_hash(phone)},
                "approval": None})
        self.save_selection()
        self.timings = {self.track["id"]: {}}
        for narrator, offset in [("autonoe", 1.0), ("charon", 3.0)]:
            cues = [{"sentenceId": sentence["id"], "syncStatus": "measured",
                     "start": offset + index * 4, "end": offset + index * 4 + 2}
                    for index, sentence in enumerate(images.content.sentences(self.track))]
            self.timings[self.track["id"]][narrator] = {
                "schemaVersion": 2, "trackId": self.track["id"], "narratorId": narrator,
                "manuscriptVersion": "v6", "textSha256": self.track["textSha256"],
                "audioSha256": hashlib.sha256(narrator.encode()).hexdigest(),
                "duration": 50.0, "sentences": cues}

    def save_selection(self):
        self.manifest_path.write_text(json.dumps(self.selection))

    def prepare(self, **changes):
        arguments = dict(root=self.root, registry=self.registry, selections_dir=self.directory,
                         timing_maps=self.timings, track_ids=[self.track["id"]])
        arguments.update(changes)
        return images.prepare_scenes(**arguments)

    def first_scene(self, result):
        return result["scenesByTrack"][self.track["id"]][0]

    def approval(self, scene):
        return {"status": "approved", "reviewer": "Fixture art reviewer", "reviewedAt": "2026-10-11T10:00:00Z",
                "sceneSha256": images.release.scene_content_hash(scene)}

    def test_private_audit_binds_each_voice_clock_and_neutral_reader_labels(self):
        original = self.manifest_path.read_bytes()
        result = self.prepare()
        scene = self.first_scene(result)
        self.assertEqual(scene["title"], "Illustration 01")
        self.assertEqual(scene["alt"], "Painted illustration for the current passage.")
        self.assertNotIn("Production prompt", json.dumps(result["scenesByTrack"]))
        audit_scene = result["audit"]["tracks"][0]["scenes"][0]
        self.assertEqual(audit_scene["sourceTitle"], self.selection["scenes"][0]["title"])
        self.assertEqual(audit_scene["narrators"]["autonoe"]["startSeconds"], 1.0)
        self.assertEqual(audit_scene["narrators"]["charon"]["startSeconds"], 3.0)
        for narrator in images.DEFAULT_NARRATORS:
            self.assertEqual(audit_scene["narrators"][narrator]["audioSha256"],
                             self.timings[self.track["id"]][narrator]["audioSha256"])
        self.assertNotIn("start", scene)
        self.assertIsNone(scene["approval"])
        self.assertEqual(result["audit"]["totals"]["pendingApprovals"], 2)
        self.assertEqual(result["audit"]["totals"]["uniqueImageFiles"], 4)
        self.assertEqual(result["audit"]["selectionSources"][0]["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.manifest_path.read_bytes(), original)

    def test_fresh_full_and_phone_hashes_reject_tampering(self):
        for field in ["sourcePath", "phone"]:
            with self.subTest(field=field):
                source = self.selection["scenes"][0]
                path = self.root / (source["sourcePath"] if field == "sourcePath" else source["phone"]["path"])
                original = path.read_bytes()
                path.write_bytes(b"tampered selected image")
                with self.assertRaisesRegex(images.content.ContentError, "image hash differs"):
                    self.prepare()
                path.write_bytes(original)

    def test_canonical_text_unknown_anchor_order_and_duplicate_id_fail(self):
        original = copy.deepcopy(self.selection)
        mutations = [lambda s: s["scenes"][0].update(sentenceTextSha256="f" * 64),
                     lambda s: s["scenes"][0].update(sentenceId="unknown"),
                     lambda s: s["scenes"].reverse(),
                     lambda s: s["scenes"][1].update(id=s["scenes"][0]["id"])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.selection = copy.deepcopy(original)
                mutate(self.selection)
                self.save_selection()
                with self.assertRaises(images.content.ContentError):
                    self.prepare()

    def test_duplicate_triggers_remain_draft_scenes_with_publication_blockers(self):
        first, second = self.selection["scenes"]
        second.update(sentenceId=first["sentenceId"], sentenceTextSha256=first["sentenceTextSha256"])
        self.save_selection()
        result = self.prepare()
        # Valid human approvals still cannot resolve two paintings competing
        # for one clock. The audit preserves actual cues and draft assets.
        metadata = {scene["id"]: {"approval": self.approval(scene)}
                    for scene in result["scenesByTrack"][self.track["id"]]}
        result = self.prepare(metadata=metadata)
        draft = result["scenesByTrack"][self.track["id"]]
        self.assertEqual([scene["id"] for scene in draft], [first["id"], second["id"]])
        self.assertTrue(all(scene["approval"] is not None for scene in draft))
        totals = result["audit"]["totals"]
        self.assertEqual(totals["duplicateTriggerGroups"], 1)
        self.assertEqual(totals["scenesWithDuplicateTrigger"], 2)
        self.assertEqual(totals["scenesMeasuredForAllNarrators"], 2)
        track = result["audit"]["tracks"][0]
        self.assertEqual(track["duplicateTriggerGroups"],
                         [{"sentenceId": first["sentenceId"], "sceneIds": [first["id"], second["id"]]}])
        for scene in track["scenes"]:
            self.assertEqual(scene["publicationBlockers"], ["duplicate-canonical-trigger"])
            self.assertEqual(scene["narrators"]["autonoe"]["startSeconds"], 1.0)
            self.assertEqual(scene["narrators"]["charon"]["startSeconds"], 3.0)
            self.assertTrue(all(clock["status"] == "measured" for clock in scene["narrators"].values()))

    def test_unknown_duplicate_and_out_of_order_track_selections_fail(self):
        for track_ids in [["chapter-999"], [self.track["id"], self.track["id"]], ["chapter-002", "chapter-001"]]:
            with self.subTest(track_ids=track_ids), self.assertRaises(images.content.ContentError):
                self.prepare(track_ids=track_ids)

    def test_backup_manifest_is_not_implicitly_selected(self):
        (self.directory / "chapter-999-selected-scenes-before-refinement.json").write_text("{}")
        result = self.prepare(track_ids=None)
        self.assertEqual(result["audit"]["trackIds"], [self.track["id"]])
        (self.directory / "chapter-999-selected-scenes.json").write_text("{}")
        with self.assertRaisesRegex(images.content.ContentError, "unknown canonical track"):
            self.prepare(track_ids=None)

    def test_source_escape_and_symlink_escape_fail_before_reading(self):
        outside = self.root.parent / (self.root.name + "-outside.webp")
        outside.write_bytes(b"outside fixture")
        self.addCleanup(outside.unlink)
        source = self.selection["scenes"][0]
        for declared in [str(outside), "../" + outside.name, str(self.directory / "escaping.webp")]:
            if declared.endswith("escaping.webp"):
                Path(declared).symlink_to(outside)
            source.update(sourcePath=declared, sha256=images.content.file_hash(outside))
            self.save_selection()
            with self.subTest(source=declared), self.assertRaisesRegex(images.content.ContentError, "escapes selected root"):
                self.prepare()

    def test_phone_path_escape_fails(self):
        self.selection["scenes"][0]["phone"]["path"] = "../outside.webp"
        self.save_selection()
        with self.assertRaisesRegex(images.content.ContentError, "escapes selected root"):
            self.prepare()

    def test_approval_is_preserved_only_for_current_neutral_reader_content(self):
        scene = self.first_scene(self.prepare())
        source = self.selection["scenes"][0]
        source.update(title=scene["title"], alt=scene["alt"], approval=self.approval(scene))
        self.save_selection()
        valid = self.first_scene(self.prepare())
        self.assertEqual(valid["approval"], source["approval"])
        source["approval"]["sceneSha256"] = "f" * 64
        self.save_selection()
        self.assertIsNone(self.first_scene(self.prepare())["approval"])

    def test_source_approval_cannot_approve_changed_reader_labels(self):
        source = self.selection["scenes"][0]
        source_hash_shape = dict(source, phoneSha256=source["phone"]["sha256"])
        source["approval"] = self.approval(source_hash_shape)
        self.save_selection()
        result = self.prepare()
        self.assertIsNone(self.first_scene(result)["approval"])
        self.assertEqual(result["audit"]["totals"]["approvedScenes"], 0)

    def test_replacing_verified_phone_bytes_requires_fresh_reader_approval(self):
        scene = self.first_scene(self.prepare())
        source = self.selection["scenes"][0]
        source["approval"] = self.approval(scene)
        self.save_selection()
        self.assertIsNotNone(self.first_scene(self.prepare())["approval"])
        phone_path = self.root / source["phone"]["path"]
        phone_path.write_bytes(b"new independently verified phone painting")
        source["phone"]["sha256"] = images.content.file_hash(phone_path)
        self.save_selection()
        self.assertIsNone(self.first_scene(self.prepare())["approval"])

    def test_full_only_selection_keeps_optional_phone_fields_absent(self):
        for source in self.selection["scenes"]:
            del source["phone"]
        self.save_selection()
        result = self.prepare()
        self.assertNotIn("phoneSourcePath", self.first_scene(result))
        self.assertNotIn("phoneSha256", self.first_scene(result))
        self.assertEqual(result["audit"]["totals"]["phoneImages"], 0)
        self.assertEqual(result["audit"]["totals"]["phoneImageBytes"], 0)

    def test_explicit_metadata_carries_current_approval_without_invention(self):
        scene = dict(self.first_scene(self.prepare()), title="Evening light", alt="A room in the current passage.")
        metadata = {scene["id"]: {"title": scene["title"], "alt": scene["alt"], "approval": self.approval(scene)}}
        prepared = self.first_scene(self.prepare(metadata=metadata))
        self.assertEqual(prepared["approval"], metadata[scene["id"]]["approval"])
        metadata[scene["id"]]["title"] = "Changed after review"
        self.assertIsNone(self.first_scene(self.prepare(metadata=metadata))["approval"])
        with self.assertRaises(images.content.ContentError):
            self.prepare(metadata={"unselected-scene": {"title": "Unused"}})

    def correction_fixture(self):
        first, second = self.selection["scenes"]
        target = next(sentence for sentence in images.content.sentences(self.track)
                      if sentence["id"] == second["sentenceId"])
        second.update(sentenceId=first["sentenceId"], sentenceTextSha256=first["sentenceTextSha256"])
        self.save_selection()
        correction = {"sourceSentenceId": second["sentenceId"],
                      "sourceSentenceTextSha256": second["sentenceTextSha256"],
                      "sourceSha256": second["sha256"], "phoneSha256": second["phone"]["sha256"],
                      "sentenceId": target["id"],
                      "sentenceTextSha256": hashlib.sha256(target["text"].encode()).hexdigest(),
                      "review": {"status": "accepted-with-note", "reviewer": "Fixture narrative reviewer",
                                 "reviewedAt": "2026-10-11T08:00:00+07:00",
                                 "note": "The second painting follows the narrated action at the next measured sentence."}}
        correction["review"]["correctionSha256"] = images.anchor_correction_hash(second["id"], correction)
        return second["id"], correction

    def test_reviewed_canonical_correction_resolves_collision_without_mutating_source_or_clocks(self):
        scene_id, correction = self.correction_fixture()
        original = self.manifest_path.read_bytes()
        self.assertEqual(self.prepare()["audit"]["totals"]["duplicateTriggerGroups"], 1)
        result = self.prepare(corrections={scene_id: correction})
        self.assertEqual(self.manifest_path.read_bytes(), original)
        self.assertEqual(result["audit"]["totals"]["duplicateTriggerGroups"], 0)
        self.assertEqual(result["audit"]["totals"]["correctedSceneAnchors"], 1)
        scene = result["audit"]["tracks"][0]["scenes"][1]
        self.assertEqual(scene["sourceSentenceId"], correction["sourceSentenceId"])
        self.assertEqual(scene["sentenceId"], correction["sentenceId"])
        for voice in ("autonoe", "charon"):
            target = next(cue for cue in self.timings[self.track["id"]][voice]["sentences"]
                          if cue["sentenceId"] == correction["sentenceId"])
            self.assertEqual(scene["narrators"][voice]["startSeconds"], target["start"])
        self.assertIsNone(result["scenesByTrack"][self.track["id"]][1]["approval"])

    def test_stale_correction_review_source_and_target_cannot_shift_reveal(self):
        scene_id, correction = self.correction_fixture()
        changes = [{"sourceSha256": "f" * 64}, {"phoneSha256": "f" * 64},
                   {"sourceSentenceId": "v6:chapter-099:p001-s01"},
                   {"sentenceId": "v6:chapter-099:p001-s01"},
                   {"sentenceTextSha256": "f" * 64}, {"start": 1.25},
                   {"review": dict(correction["review"], correctionSha256="f" * 64)},
                   {"review": dict(correction["review"], reviewedAt="2026-10-11T08:00:00")}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(images.content.ContentError):
                self.prepare(corrections={scene_id: dict(correction, **change)})
        with self.assertRaisesRegex(images.content.ContentError, "unselected scene"):
            self.prepare(corrections={"unselected-scene": correction})

    def test_source_bound_reader_metadata_rejects_changed_art_or_selection(self):
        source = self.selection["scenes"][0]
        row = {"title": "Evening light", "alt": "Warm light across a room.",
               "sourceSha256": source["sha256"], "phoneSha256": source["phone"]["sha256"],
               "sourceManifestSha256": images.content.file_hash(self.manifest_path)}
        self.assertEqual(self.first_scene(self.prepare(metadata={source["id"]: row}))["alt"], row["alt"])
        for key in ("sourceSha256", "phoneSha256", "sourceManifestSha256"):
            with self.subTest(key=key), self.assertRaisesRegex(images.content.ContentError, "selected source changed"):
                self.prepare(metadata={source["id"]: dict(row, **{key: "f" * 64})})

    def test_unavailable_missing_cue_and_missing_narrator_keep_null_clocks(self):
        track_maps = self.timings[self.track["id"]]
        gap = track_maps["autonoe"]["sentences"][0]
        gap.update(syncStatus="unavailable", start=None, end=None, reason="low-confidence-ASR-anchor")
        track_maps["charon"]["sentences"].pop(0)
        audit = self.prepare()["audit"]["tracks"][0]["scenes"][0]
        for narrator, reason in [("autonoe", "low-confidence-ASR-anchor"), ("charon", "missing-sentence-cue")]:
            self.assertEqual(audit["narrators"][narrator]["reason"], reason)
            self.assertIsNone(audit["narrators"][narrator]["startSeconds"])
        track_maps.pop("charon")
        audit = self.prepare()["audit"]["tracks"][0]["scenes"][0]
        self.assertEqual(audit["narrators"]["charon"]["reason"], "missing-timing-map")
        self.assertIsNone(audit["narrators"]["charon"]["audioSha256"])

    def test_scene_only_onset_audit_keeps_full_sentence_unmeasured_and_checks_recording(self):
        timing = self.timings[self.track["id"]]["autonoe"]
        cue = timing["sentences"][0]
        cue.update(syncStatus="unavailable", start=None, end=None, reason="low-confidence-ASR-anchor")
        original = copy.deepcopy(timing)
        clock = {"schemaVersion": 1, "kind": "measured-scene-cues", "trackId": self.track["id"],
                 "narratorId": "autonoe", "manuscriptVersion": "v6", "textSha256": self.track["textSha256"],
                 "audioSha256": timing["audioSha256"], "duration": timing["duration"],
                 "cues": [{"sentenceId": cue["sentenceId"], "start": 1.25, "method": "unique-measured-ASR-prefix"}]}
        result = self.prepare(scene_timing_maps={self.track["id"]: {"autonoe": clock}})
        audit = result["audit"]["tracks"][0]["scenes"][0]
        self.assertEqual(audit["narrators"]["autonoe"]["startSeconds"], 1.25)
        self.assertEqual(audit["narrators"]["autonoe"]["source"], "measured-scene-cue")
        self.assertEqual(result["audit"]["totals"]["measuredSceneOnlyAnchors"], 1)
        self.assertEqual(timing, original)
        for change in [{"audioSha256": "f" * 64}, {"textSha256": "f" * 64},
                       {"cues": clock["cues"] * 2}, {"cues": [dict(clock["cues"][0], start=float('nan'))]}]:
            with self.subTest(change=change), self.assertRaises(images.content.ContentError):
                self.prepare(scene_timing_maps={self.track["id"]: {"autonoe": dict(clock, **change)}})
        for second_start in [1.25, 1.0]:
            rows = clock["cues"] + [dict(clock["cues"][0], sentenceId=timing["sentences"][1]["sentenceId"],
                                         start=second_start)]
            with self.subTest(second_start=second_start), self.assertRaisesRegex(images.content.ContentError, "strictly increasing"):
                self.prepare(scene_timing_maps={self.track["id"]: {"autonoe": dict(clock, cues=rows)}})

    def test_unavailable_cue_with_estimated_clock_and_stale_timing_identity_fail(self):
        original = copy.deepcopy(self.timings)
        for mutate in [lambda m: m["sentences"][0].update(syncStatus="unavailable", reason="low-confidence-ASR-anchor"),
                       lambda m: m.update(audioSha256="unknown"),
                       lambda m: m.update(textSha256="f" * 64),
                       lambda m: m["sentences"].reverse(),
                       lambda m: m["sentences"].append(copy.deepcopy(m["sentences"][0]))]:
            self.timings = copy.deepcopy(original)
            mutate(self.timings[self.track["id"]]["charon"])
            with self.assertRaises(images.content.ContentError):
                self.prepare()

    def test_shared_source_timestamps_and_unsafe_metadata_are_rejected(self):
        self.selection["scenes"][0]["start"] = 1.0
        self.save_selection()
        with self.assertRaisesRegex(images.content.ContentError, "narrator clock"):
            self.prepare()
        del self.selection["scenes"][0]["start"]
        self.save_selection()
        with self.assertRaisesRegex(images.content.ContentError, "Unsafe"):
            self.prepare(metadata={"painting-1": {"alt": "<script>unsafe</script>"}})

    def test_private_immutable_output_reuse_permissions_and_containment(self):
        prepared = self.prepare()
        output = self.root / "private-output"
        first = images.write_prepared(self.root, prepared, output)
        second = images.write_prepared(self.root, prepared, output)
        self.assertEqual(first, second)
        self.assertEqual(stat.S_IMODE(Path(first["scenesPath"]).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        Path(first["scenesPath"]).write_bytes(b"tampered immutable output")
        with self.assertRaisesRegex(images.content.ContentError, "immutable asset changed"):
            images.write_prepared(self.root, prepared, output)
        for destination in [self.root, self.root / "web/dist/private", self.root.parent / "outside-output"]:
            with self.subTest(destination=destination), self.assertRaises(images.content.ContentError):
                images.write_prepared(self.root, prepared, destination)

    def test_output_child_symlink_cannot_redirect_audit_writes(self):
        output = self.root / "private-output"
        output.mkdir()
        outside = self.root / "other-private-directory"
        outside.mkdir()
        (output / "tracks").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(images.content.ContentError, "symlink|containment"):
            images.write_prepared(self.root, self.prepare(), output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_cli_prints_summary_and_private_paths_without_story_metadata(self):
        registry_path, timing_path = self.root / "registry.json", self.root / "timings.json"
        registry_path.write_text(json.dumps(self.registry))
        timing_path.write_text(json.dumps(self.timings))
        argv = ["prepare_illustrated_scenes.py", "--root", str(self.root), "--registry", str(registry_path),
                "--selections", str(self.directory), "--timings", str(timing_path),
                "--out", str(self.root / "private-output")]
        stdout = io.StringIO()
        with patch.object(sys, "argv", argv), redirect_stdout(stdout):
            images.main()
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["summary"]["scenes"], 2)
        self.assertNotIn("Production prompt", stdout.getvalue())
        self.assertTrue(Path(result["scenesPath"]).is_file())
        self.assertTrue(Path(result["auditPath"]).is_file())

    def test_cli_changed_input_cannot_be_bound_to_old_prepared_data(self):
        registry_path, timing_path = self.root / "registry.json", self.root / "timings.json"
        registry_path.write_text(json.dumps(self.registry))
        timing_path.write_text(json.dumps(self.timings))
        output = self.root / "private-output"
        argv = ["prepare_illustrated_scenes.py", "--root", str(self.root), "--registry", str(registry_path),
                "--selections", str(self.directory), "--timings", str(timing_path), "--out", str(output)]
        original_prepare = images.prepare_scenes
        def replace_input(*args, **kwargs):
            result = original_prepare(*args, **kwargs)
            registry_path.write_bytes(b"changed registry after verification")
            return result
        with patch.object(sys, "argv", argv), patch.object(images, "prepare_scenes", side_effect=replace_input):
            with self.assertRaisesRegex(images.content.ContentError, "input changed during verification"):
                images.main()
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
