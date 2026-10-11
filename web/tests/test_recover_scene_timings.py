"""Painting onsets require measured unique speech and never estimate prose clocks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web/scripts"))
import recover_scene_timings as recovery


class SceneClockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sentences = [
            {"id": "v6:chapter-001:p001-s01", "text": "The first measured passage ends here."},
            {"id": "v6:chapter-001:p001-s02", "text": "Adrian reached across the kitchen table before the lost ending."},
            {"id": "v6:chapter-001:p001-s03", "text": "The following measured passage starts here."}]
        self.track = {"id": "chapter-001", "manuscriptVersion": "v6", "textSha256": "a" * 64,
                      "paragraphs": [{"sentences": self.sentences}]}
        self.target = self.sentences[1]["id"]
        self.baseline = {"schemaVersion": 2, "trackId": self.track["id"], "narratorId": "charon",
                         "manuscriptVersion": "v6", "textSha256": self.track["textSha256"],
                         "audioSha256": "b" * 64, "duration": 30.0, "sourceBindings": {}, "evidence": [],
                         "sentences": [
                             {"sentenceId": self.sentences[0]["id"], "start": 1.0, "end": 5.0, "syncStatus": "measured"},
                             {"sentenceId": self.target, "start": None, "end": None, "syncStatus": "unavailable",
                              "reason": "low-confidence-ASR-anchor", "recheckWindow": {"start": 5.0, "end": 20.0,
                                  "beforeSentenceId": self.sentences[0]["id"], "afterSentenceId": self.sentences[2]["id"],
                                  "scope": "neighbor-recheck-only"}},
                             {"sentenceId": self.sentences[2]["id"], "start": 20.0, "end": 25.0, "syncStatus": "measured"}]}
        self.words = [(word, 7.12 + i, 7.32 + i)
                      for i, word in enumerate("Adrian reached across the kitchen table".split())]
        self.recording = {"trackId": self.track["id"], "narratorId": "charon", "sha256": "b" * 64,
                          "decodedDuration": 30.0, "selectedClips": []}

    def recover(self, words=None, targets=None):
        return recovery.measured_prefix_cues(self.track, self.baseline,
                                             self.words if words is None else words,
                                             [self.target] if targets is None else targets)

    def test_measured_prefix_restores_only_image_onset_and_leaves_sentence_null(self):
        original = copy.deepcopy(self.baseline)
        cues, blocked = self.recover()
        self.assertEqual(blocked, [])
        self.assertEqual(cues[0]["start"], 7.12)
        self.assertEqual(cues[0]["prefixTokens"], ["adrian", "reached", "across", "the", "kitchen", "table"])
        self.assertNotIn("end", cues[0])
        self.assertEqual(self.baseline, original)
        self.assertIsNone(self.baseline["sentences"][1]["start"])
        self.assertIsNone(self.baseline["sentences"][1]["end"])

    def test_no_interior_word_or_next_sentence_can_replace_missing_first_word(self):
        cues, blocked = self.recover(self.words[1:])
        self.assertEqual(cues, [])
        self.assertEqual(blocked[0]["reason"], "missing-exact-prefix")
        self.assertNotIn("start", blocked[0])

    def test_observed_repeated_phrase_is_ambiguous(self):
        repeated = self.words + [(word, start + 7, end + 7) for word, start, end in self.words]
        cues, blocked = self.recover(repeated)
        self.assertEqual(cues, [])
        self.assertEqual(blocked[0]["reason"], "ambiguous-observed-prefix")

    def test_every_prefix_word_stays_in_gap_and_before_its_endpoint(self):
        for end in [13.0, 21.0]:
            words = list(self.words)
            word, start, _ = words[1]
            words[1] = (word, start, end)
            with self.subTest(end=end):
                cues, blocked = self.recover(words)
                self.assertEqual(cues, [])
                self.assertEqual(blocked[0]["reason"], "missing-exact-prefix")

    def test_short_prefix_target_words_cannot_borrow_following_sentence_bounds(self):
        self.sentences[1]["text"] = "Yes indeed."
        words = [(word, start, end) for word, start, end in [
            ("Yes", 18.0, 18.2), ("indeed", 18.5, 20.1), ("The", 20.0, 20.2),
            ("following", 20.5, 20.7), ("measured", 21.0, 21.2), ("passage", 21.5, 21.7)]]
        cues, blocked = self.recover(words)
        self.assertEqual(cues, [])
        self.assertEqual(blocked[0]["reason"], "missing-exact-prefix")

    def test_numeric_prefix_keeps_every_contributing_word_interval(self):
        self.sentences[1]["text"] = "42 people crossed the empty street."
        words = [(word, 7.12 + index * .5, 7.32 + index * .5)
                 for index, word in enumerate("Forty two people crossed the empty street".split())]
        words[0] = ("Forty", words[0][1], 21.0)
        cues, blocked = self.recover(words)
        self.assertEqual(cues, [])
        self.assertEqual(blocked[0]["reason"], "missing-exact-prefix")

    def test_reversed_unique_targets_in_one_gap_remain_unavailable(self):
        lamp = {"id": "v6:chapter-001:p001-s02", "text": "The lamp beside the window glowed softly."}
        door = {"id": "v6:chapter-001:p001-s03", "text": "The door across the hallway opened slowly."}
        after = {"id": "v6:chapter-001:p001-s04", "text": self.sentences[2]["text"]}
        self.track["paragraphs"][0]["sentences"] = [self.sentences[0], lamp, door, after]
        gap = self.baseline["sentences"][1]
        gap["recheckWindow"]["afterSentenceId"] = after["id"]
        second_gap = copy.deepcopy(gap)
        second_gap["sentenceId"] = door["id"]
        self.baseline["sentences"][2]["sentenceId"] = after["id"]
        self.baseline["sentences"].insert(2, second_gap)
        words = [(word, 6 + index * 0.3, 6.2 + index * 0.3)
                 for index, word in enumerate("The door across the hallway opened".split())]
        words += [(word, 10 + index * 0.3, 10.2 + index * 0.3)
                  for index, word in enumerate("The lamp beside the window glowed".split())]
        cues, blocked = self.recover(words, [lamp["id"], door["id"]])
        self.assertEqual(cues, [])
        self.assertEqual([gap["sentenceId"] for gap in blocked], [lamp["id"], door["id"]])
        self.assertTrue(all(gap["reason"] == "nonmonotone-measured-scene-onsets" for gap in blocked))
        self.assertTrue(all("start" not in gap for gap in blocked))

    def test_removing_an_inverted_pair_cannot_expose_an_accepted_clock_conflict(self):
        target_ids = [f"sentence-{index}" for index in range(6)]
        cues = [{"sentenceId": sentence_id, "start": start}
                for sentence_id, start in zip(target_ids, [3.0, 4.0, 5.0, 1.0, 2.0, 3.0])]
        retained, blocked = recovery.retain_monotone_cues(cues, [], target_ids)
        self.assertEqual(retained, [])
        self.assertEqual([gap["sentenceId"] for gap in blocked], target_ids)
        self.assertTrue(all("start" not in gap for gap in blocked))

    def test_same_canonical_phrase_twice_in_gap_is_ambiguous(self):
        self.sentences[1]["text"] += " Adrian reached across the kitchen table again."
        cues, blocked = self.recover()
        self.assertEqual(cues, [])
        self.assertEqual(blocked[0]["reason"], "ambiguous-canonical-prefix")

    def test_shorter_exact_prefix_must_be_unique_across_entire_chapter(self):
        words = self.words[:4] + [("unrecognized", 11.12, 11.32)]
        cues, blocked = self.recover(words)
        self.assertEqual(blocked, [])
        self.assertEqual(cues[0]["prefixTokens"], ["adrian", "reached", "across", "the"])
        self.sentences[0]["text"] = "Adrian reached across the street."
        # Four words still uniquely identify the target; three would also fit
        # the earlier measured sentence and cannot be promoted from this gap.
        cues, blocked = self.recover(words[:3])
        self.assertEqual(cues, [])
        self.assertTrue(blocked)

    def test_speech_outside_original_neighbor_bounds_is_not_promoted(self):
        for offset in [-5, 14]:
            with self.subTest(offset=offset):
                cues, blocked = self.recover([(word, start + offset, end + offset) for word, start, end in self.words])
                self.assertEqual(cues, [])
                self.assertTrue(blocked)

    def test_short_sentence_uses_following_context_but_keeps_its_own_start(self):
        self.sentences[1]["text"] = "Yes indeed."
        words = [(word, start, start + 0.2) for word, start in [
            ("Yes", 18.0), ("indeed", 18.5), ("The", 20.0), ("following", 20.5), ("measured", 21.0), ("passage", 21.5)]]
        cues, blocked = self.recover(words)
        self.assertEqual(blocked, [])
        self.assertEqual(cues[0]["start"], 18.0)
        self.assertEqual(cues[0]["prefixTokens"], ["yes", "indeed", "the", "following", "measured", "passage"])

    def test_invalid_target_order_or_existing_measured_sentence_fails(self):
        for targets in [[self.target, self.target], ["unknown"], [self.sentences[0]["id"]]]:
            with self.subTest(targets=targets), self.assertRaises(recovery.content.ContentError):
                self.recover(targets=targets)

    def test_independent_voice_clocks_copy_their_actual_first_word(self):
        first, _ = self.recover()
        second, _ = self.recover([(word, start + 0.5, end + 0.5) for word, start, end in self.words])
        self.assertEqual(first[0]["start"], 7.12)
        self.assertEqual(second[0]["start"], 7.62)

    def test_existing_numeric_normalization_uses_first_measured_spoken_word(self):
        self.sentences[1]["text"] = "42 people crossed the empty street."
        words = [(word, 7.12 + index, 7.32 + index)
                 for index, word in enumerate("Forty two people crossed the empty street".split())]
        cues, blocked = self.recover(words)
        self.assertEqual(blocked, [])
        self.assertEqual(cues[0]["prefixTokens"][0], "num:42")
        self.assertEqual(cues[0]["start"], 7.12)

    def scene_map(self):
        asset = recovery.content.export_asset(self.root, self.track["id"], "baseline", self.baseline)
        return recovery.scene_clock_map(self.track, self.recording, self.baseline, self.words,
                    [self.target], [], asset["path"], asset["sha256"])

    def phrase_fixture(self, *, span=(4, 10)):
        self.sentences[1]["text"] = "Adrian drove toward Zurich, and the doubt drove with him."
        sentence_hash = hashlib.sha256(self.sentences[1]["text"].encode()).hexdigest()
        full = self.root / "selected-full.webp"
        phone = self.root / "selected-phone.webp"
        full.write_bytes(b"selected full image")
        phone.write_bytes(b"selected phone image")
        self.source = {"id": "scene-driving", "trackId": self.track["id"], "sentenceId": self.target,
                       "sentenceTextSha256": sentence_hash, "sourcePath": full.name,
                       "sha256": recovery.content.file_hash(full), "phone": {"path": phone.name,
                       "sha256": recovery.content.file_hash(phone)}}
        manifest = recovery.content.export_asset(self.root, self.track["id"], "selected",
                                                 {"chapterId": self.track["id"], "scenes": [self.source]})
        request = {"schemaVersion": 1, "kind": "reviewed-scene-phrase-end", "trackId": self.track["id"],
                   "narratorId": self.recording["narratorId"], "sceneId": self.source["id"],
                   "sourceManifestPath": manifest["path"], "sourceManifestSha256": manifest["sha256"],
                   "sourceSha256": self.source["sha256"], "phoneSha256": self.source["phone"]["sha256"],
                   "sentenceId": self.target, "sentenceTextSha256": sentence_hash,
                   "tokenStart": span[0], "tokenEnd": span[1], "boundary": "end"}
        request["review"] = {"status": "accepted-with-note", "reviewer": "narrative reviewer",
                             "reviewedAt": "2026-10-11T03:00:00Z", "note": "Driving disclosed by this phrase end.",
                             "requestSha256": recovery.reviewed_phrase_hash(request)}
        self.phrase_words = [(word, 7.12 + index * 0.5, 7.32 + index * 0.5)
                             for index, word in enumerate("Mauro drove towards Zurich and the doubt drove with him".split())]
        return request, self.write_phrase_request(request)

    def write_phrase_request(self, request):
        asset = recovery.content.export_asset(self.root, self.track["id"], "phrase-request", request)
        return {"path": asset["path"], "sha256": asset["sha256"]}

    def phrase_map(self, reference, words=None):
        asset = recovery.content.export_asset(self.root, self.track["id"], "baseline", self.baseline)
        return recovery.scene_clock_map(self.track, self.recording, self.baseline,
                    self.phrase_words if words is None else words, [self.target], [], asset["path"], asset["sha256"],
                    reviewed_phrase_requests=[reference], root=self.root)

    def test_explicit_reviewed_phrase_end_uses_final_word_clock_and_keeps_prose_unavailable(self):
        request, reference = self.phrase_fixture()
        original = copy.deepcopy(self.baseline)
        self.assertEqual(self.recover(self.phrase_words)[0], [])
        result = self.phrase_map(reference)
        cue = result["cues"][0]
        self.assertEqual(result["unavailable"], [])
        self.assertEqual(cue["method"], recovery.PHRASE_METHOD)
        self.assertEqual(cue["start"], self.phrase_words[-1][2])
        self.assertEqual(cue["phraseTokens"], "and the doubt drove with him".split())
        self.assertEqual(cue["sceneId"], self.source["id"])
        self.assertEqual(cue["sourceSha256"], self.source["sha256"])
        self.assertEqual(cue["phoneSha256"], self.source["phone"]["sha256"])
        self.assertEqual(self.baseline, original)
        self.assertIsNone(self.baseline["sentences"][1]["end"])

    def test_numeric_phrase_keeps_support_without_inventing_a_later_endpoint(self):
        request, _ = self.phrase_fixture(span=(2, 8))
        self.sentences[1]["text"] = "She waited with 42 people beside the station."
        digest = hashlib.sha256(self.sentences[1]["text"].encode()).hexdigest()
        self.source["sentenceTextSha256"] = digest
        manifest = recovery.content.export_asset(self.root, self.track["id"], "selected",
                                                 {"chapterId": self.track["id"], "scenes": [self.source]})
        request.update(sentenceTextSha256=digest, sourceManifestPath=manifest["path"],
                       sourceManifestSha256=manifest["sha256"])
        request["review"]["requestSha256"] = recovery.reviewed_phrase_hash(request)
        words = [(word, 7.12 + index * .5, 7.32 + index * .5)
                 for index, word in enumerate("They waited with Forty two people beside the station".split())]
        for span, end in [((2, 8), 21.0), ((2, 8), 12.0), ((1, 4), 11.0)]:
            changed = copy.deepcopy(request)
            changed.update(tokenStart=span[0], tokenEnd=span[1])
            changed["review"]["requestSha256"] = recovery.reviewed_phrase_hash(changed)
            bad = list(words)
            bad[3] = (bad[3][0], bad[3][1], end)
            with self.subTest(span=span, end=end):
                result = self.phrase_map(self.write_phrase_request(changed), bad)
                self.assertEqual(result["cues"], [])
                self.assertEqual(result["unavailable"][0]["reason"], "missing-exact-reviewed-phrase")

    def test_full_only_reviewed_phrase_binds_full_image_and_null_phone(self):
        request, _ = self.phrase_fixture()
        source = copy.deepcopy(self.source)
        del source["phone"]
        for selected in [source, dict(source, phone=None)]:
            manifest = recovery.content.export_asset(self.root, self.track["id"], "selected",
                                                     {"chapterId": self.track["id"], "scenes": [selected]})
            request.update(sourceManifestPath=manifest["path"], sourceManifestSha256=manifest["sha256"],
                           phoneSha256=None)
            request["review"]["requestSha256"] = recovery.reviewed_phrase_hash(request)
            reference = self.write_phrase_request(request)
            result = self.phrase_map(reference)
            cue = result["cues"][0]
            self.assertEqual(result["unavailable"], [])
            self.assertIsNone(cue["phoneSha256"])
            self.assertEqual(cue["start"], self.phrase_words[-1][2])
            recovery.content.validate_scene_onset_binding(source, cue, source)
        (self.root / "selected-full.webp").write_bytes(b"changed full-only painting")
        with self.assertRaisesRegex(recovery.content.ContentError, "image bytes changed"):
            self.phrase_map(reference)

    def test_reviewed_phrase_rejects_stale_missing_phone_and_malformed_present_phone(self):
        request, _ = self.phrase_fixture()
        original_phone = self.source["phone"]
        cases = [(None, original_phone["sha256"], "painting binding differs"),
                 ("invalid", None, "phone binding is invalid"),
                 ({}, None, "each selected image path and hash"),
                 ({"sha256": original_phone["sha256"]}, original_phone["sha256"],
                  "each selected image path and hash"),
                 ({"path": original_phone["path"], "sha256": "invalid"}, "invalid",
                  "each selected image path and hash")]
        for phone, digest, error in cases:
            source = dict(self.source, phone=phone)
            manifest = recovery.content.export_asset(self.root, self.track["id"], "selected",
                                                     {"chapterId": self.track["id"], "scenes": [source]})
            request.update(sourceManifestPath=manifest["path"], sourceManifestSha256=manifest["sha256"],
                           phoneSha256=digest)
            request["review"]["requestSha256"] = recovery.reviewed_phrase_hash(request)
            with self.subTest(phone=phone), self.assertRaisesRegex(recovery.content.ContentError, error):
                self.phrase_map(self.write_phrase_request(request))

    def test_reviewed_phrase_request_hash_review_and_selected_bytes_are_freshly_bound(self):
        request, reference = self.phrase_fixture()
        for field, value in [("sceneId", "other-painting"), ("sourceSha256", "f" * 64),
                             ("phoneSha256", "f" * 64), ("sentenceTextSha256", "f" * 64),
                             ("tokenStart", 5)]:
            changed = copy.deepcopy(request)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(recovery.content.ContentError):
                self.phrase_map(self.write_phrase_request(changed))
        changed = copy.deepcopy(request)
        changed["review"]["reviewedAt"] = "2026-10-11T03:00:00"
        with self.assertRaisesRegex(recovery.content.ContentError, "timezone"):
            self.phrase_map(self.write_phrase_request(changed))
        for name in ["selected-full.webp", "selected-phone.webp"]:
            path = self.root / name
            prior = path.read_bytes()
            path.write_bytes(b"changed selected image")
            with self.subTest(image=name), self.assertRaisesRegex(recovery.content.ContentError, "image bytes changed"):
                self.phrase_map(reference)
            path.write_bytes(prior)
        path = self.root / reference["path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(recovery.content.ContentError, "request bytes changed"):
            self.phrase_map(reference)

    def test_reviewed_phrase_span_must_be_strict_canonical_single_sentence_and_unique(self):
        for span in [(True, 6), (4, False), (4, 11), (4, 6), (-1, 4)]:
            with self.subTest(span=span):
                request, reference = self.phrase_fixture(span=span)
                with self.assertRaisesRegex(recovery.content.ContentError, "token span"):
                    self.phrase_map(reference)
        request, reference = self.phrase_fixture()
        self.sentences[0]["text"] = "The other thought ended and the doubt drove with him."
        with self.assertRaisesRegex(recovery.content.ContentError, "not unique"):
            self.phrase_map(reference)

    def test_missing_repeated_or_outside_gap_reviewed_phrases_remain_unavailable(self):
        request, reference = self.phrase_fixture()
        cases = [self.phrase_words[:-1],
                 self.phrase_words + [(word, start + 6, end + 6) for word, start, end in self.phrase_words],
                 [(word, start - 5, end - 5) for word, start, end in self.phrase_words],
                 [(word, start + 10, end + 10) for word, start, end in self.phrase_words]]
        for words in cases:
            with self.subTest(words=words):
                result = self.phrase_map(reference, words)
                self.assertEqual(result["cues"], [])
                self.assertNotIn("start", result["unavailable"][0])

    def test_every_matched_phrase_word_must_end_within_gap_and_before_selected_endpoint(self):
        request, reference = self.phrase_fixture()
        for changed_end in [21.0, 13.0]:
            with self.subTest(changed_end=changed_end):
                words = list(self.phrase_words)
                word, start, _ = words[4]
                words[4] = word, start, changed_end
                result = self.phrase_map(reference, words)
                self.assertEqual(result["cues"], [])
                self.assertEqual(result["unavailable"][0]["reason"], "missing-exact-reviewed-phrase")
                self.assertNotIn("start", result["unavailable"][0])

    def test_phrase_map_verifier_reproduces_endpoint_span_and_request(self):
        request, reference = self.phrase_fixture()
        result = self.phrase_map(reference)
        with patch.object(recovery.finalize, "verify_technical_alignment", return_value=(self.recording, {})), \
                patch.object(recovery, "checked_scene_words", return_value=self.phrase_words), \
                patch.object(recovery.finalize, "checked_cache_words", return_value=self.phrase_words):
            _, proof = recovery.verify_scene_clock(self.root, self.track, self.recording, self.baseline, result)
            self.assertEqual(proof["measuredCues"], 1)
            for field in ["start", "phraseEnd", "observedTokenIndex", "tokenStart", "sceneId", "phoneSha256"]:
                changed = copy.deepcopy(result)
                changed["cues"][0][field] = "wrong" if isinstance(changed["cues"][0][field], str) else 1.0
                with self.subTest(field=field), self.assertRaisesRegex(recovery.content.ContentError, "reproducible"):
                    recovery.verify_scene_clock(self.root, self.track, self.recording, self.baseline, changed)
            changed = copy.deepcopy(result)
            changed["reviewedPhraseRequests"][0]["sha256"] = "f" * 64
            with self.assertRaisesRegex(recovery.content.ContentError, "request bytes changed"):
                recovery.verify_scene_clock(self.root, self.track, self.recording, self.baseline, changed)

    def test_verified_map_rebuild_rejects_forged_seconds_or_recording_identity(self):
        scene_map = self.scene_map()
        with patch.object(recovery.finalize, "verify_technical_alignment", return_value=(self.recording, {})), \
                patch.object(recovery, "checked_scene_words", return_value=self.words), \
                patch.object(recovery.finalize, "checked_cache_words", return_value=self.words):
            verified, proof = recovery.verify_scene_clock(self.root, self.track, self.recording, self.baseline, scene_map)
            self.assertEqual(proof["measuredCues"], 1)
            for key in ["cues", "audioSha256", "observedTokenIndex"]:
                changed = copy.deepcopy(scene_map)
                if key == "cues":
                    changed["cues"][0]["start"] += 0.1
                elif key == "observedTokenIndex":
                    changed["cues"][0]["observedTokenIndex"] = False
                else:
                    changed[key] = "f" * 64
                with self.subTest(key=key), self.assertRaisesRegex(recovery.content.ContentError, "reproducible"):
                    recovery.verify_scene_clock(self.root, self.track, verified, self.baseline, changed)

    def test_baseline_prefix_proof_survives_an_incomplete_refined_recognition(self):
        asset = recovery.content.export_asset(self.root, self.track["id"], "baseline", self.baseline)
        result = recovery.scene_clock_map(self.track, self.recording, self.baseline, self.words[1:],
                     [self.target], [], asset["path"], asset["sha256"], self.words)
        self.assertEqual(result["unavailable"], [])
        self.assertEqual(result["cues"][0]["wordSource"], "baseline-ASR")
        self.assertEqual(result["cues"][0]["start"], 7.12)

    def test_baseline_file_tampering_fails_before_verifier(self):
        scene_map = self.scene_map()
        (self.root / scene_map["baselineTimingPath"]).write_bytes(b"tampered private baseline")
        with patch.object(recovery.finalize, "verify_technical_alignment") as verify:
            with self.assertRaisesRegex(recovery.content.ContentError, "baseline bytes differ"):
                recovery.verify_scene_clock(self.root, self.track, self.recording, self.baseline, scene_map)
            verify.assert_not_called()

    def test_boolean_schema_version_is_rejected_before_evidence_reads(self):
        scene_map = self.scene_map()
        scene_map["schemaVersion"] = True
        with patch.object(recovery.finalize, "verify_technical_alignment") as verify:
            with self.assertRaisesRegex(recovery.content.ContentError, "clock identity"):
                recovery.verify_scene_clock(self.root, self.track, self.recording, self.baseline, scene_map)
            verify.assert_not_called()

    def test_private_output_refuses_public_or_external_destinations(self):
        for destination in [self.root, self.root / "web/dist/clocks", self.root.parent / "external-scene-clocks"]:
            with self.subTest(destination=destination), self.assertRaises(recovery.content.ContentError):
                recovery.private_output(self.root, destination)

    def test_recognition_root_resolves_owned_directory_and_rejects_escape_symlinks(self):
        private = self.root / "retained-recognition"
        private.mkdir()
        self.assertEqual(recovery.contained_recognition_root(self.root, Path("retained-recognition")), private)
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary).resolve()
            escape = self.root / "escaped-recognition"
            escape.symlink_to(outside, target_is_directory=True)
            public_alias = self.root / "public-recognition"
            public_alias.symlink_to(public, target_is_directory=True)
            for path in [outside, escape, public, public_alias, self.root / "missing"]:
                with self.subTest(path=path), self.assertRaises(recovery.content.ContentError):
                    recovery.contained_recognition_root(self.root, path)

    def refinement_fixture(self):
        clip_id, request_hash = "chapter-001-001", "c" * 64
        text = self.sentences[1]["text"]
        clip = {"id": clip_id, "rawAudioSha256": "d" * 64, "requestSha256": request_hash,
                "textSha256": hashlib.sha256(text.encode()).hexdigest()}
        raw_path = self.root / "Audiobook/v8/raw/chapter-001-001.wav"
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(b"fixture existing unchanged raw recording")
        clip.update(sourcePath=raw_path.relative_to(self.root).as_posix(),
                    rawAudioSha256=recovery.content.file_hash(raw_path))
        recording = dict(self.recording, productionEdition="v8", sourcePath="Audiobook/v8/mastered/chapter-001.mp3",
                         selectedClips=[clip])
        qa = {"assembly": {"internal_silence_removed": False, "source_samples": 3000,
                "leading_source_samples_trimmed": 0, "trailing_source_samples_trimmed": 0,
                "body_samples": 3000, "added_head_samples": 0, "added_tail_samples": 0},
              "coordinator_checks": {"sample_rate": 100, "expected_master_samples": 3000,
                "chunk_timeline": [{"id": clip_id, "source_start_seconds": 0.0, "source_end_seconds": 30.0,
                                    "master_start_seconds": 0.0, "master_end_seconds": 30.0, "retained_samples": 3000}]}}
        qa_path = self.root / "Audiobook/v8/mastered/chapter-001.qa.json"
        qa_path.parent.mkdir(parents=True)
        qa_path.write_text(json.dumps(qa))
        manifest_path = self.root / "Audiobook/v8/generation-manifest.json"
        manifest_path.write_text(json.dumps({"items": [{"id": clip_id, "text": text, "request_sha256": request_hash}]}))
        clock_words = [{"word": word, "start": start, "end": end} for word, start, end in self.words]
        base = {"id": clip_id, "expected_inputtext": text, "duration_seconds": 30.0,
                "cache_identity": {"audio_sha256": clip["rawAudioSha256"], "request_sha256": request_hash,
                                   "inputtext_sha256": clip["textSha256"]}, "segments": [{"words": clock_words}]}
        base_asset = recovery.content.export_asset(self.root, self.track["id"], "base-asr", base)
        prior = recovery.evidence_row(self.root, self.root / base_asset["path"], base, clip)
        baseline = dict(self.baseline, evidence=[prior])
        model_path = self.root / "Audiobook/v6/asr-model-medium"
        model_path.mkdir(parents=True)
        for name in ["config.json", "weights.npz"]:
            (model_path / name).write_bytes(b"fixture installed model identity")
        model = {"path": str(model_path), "files_sha256": {name: recovery.content.file_hash(model_path / name)
                                                         for name in ["config.json", "weights.npz"]}}
        call = {"language": "en", "temperature": 0.0, "condition_on_previous_text": False,
                "verbose": None, "word_timestamps": True, "clip_timestamps": "5.0,15.0"}
        result_record = {"identity": {"rawAudioSha256": clip["rawAudioSha256"], "baseCacheSha256": prior["cacheSha256"],
                            "modelIdentity": model, "decodeSettings": call}, "result": {"segments": [{"words": clock_words}]}}
        result_asset = recovery.content.export_asset(self.root, self.track["id"], "recognition", result_record)
        outcome = {"start": 5.0, "end": 15.0, "status": "measured", "modelSha256": model["files_sha256"],
                   "resultPath": result_asset["path"], "resultSha256": result_asset["sha256"]}
        lineage = {"method": recovery.METHOD, "baseCacheSha256": prior["cacheSha256"], "modelIdentity": model,
                   "decodeCalls": [call], "recognitionEvidence": [outcome], "windows": [[5.0, 15.0]]}
        refined = dict(base, cache_identity=dict(base["cache_identity"], sceneRefinement=lineage),
                       refinementAttempts=[outcome])
        refined_asset = recovery.content.export_asset(self.root, self.track["id"], "refined-asr", refined)
        evidence = [recovery.evidence_row(self.root, self.root / refined_asset["path"], refined, clip)]
        return recording, baseline, evidence, result_asset, refined, refined_asset

    def test_refined_cache_reproduces_retained_model_result_and_rejects_result_tampering(self):
        recording, baseline, evidence, result_asset, _, _ = self.refinement_fixture()
        with patch.object(recovery.finalize, "checked_cache_words", return_value=self.words):
            words = recovery.checked_scene_words(self.root, self.track, recording, baseline, evidence)
            self.assertEqual(words, self.words)
            (self.root / result_asset["path"]).write_bytes(b"changed recognizer output")
            with self.assertRaisesRegex(recovery.content.ContentError, "recognizer output bytes changed"):
                recovery.checked_scene_words(self.root, self.track, recording, baseline, evidence)

    def test_forged_refined_word_clock_fails_even_with_matching_cache_digest(self):
        recording, baseline, evidence, _, refined, refined_asset = self.refinement_fixture()
        refined["segments"][0]["words"][0]["start"] += 0.1
        path = self.root / refined_asset["path"]
        path.write_bytes(recovery.content.json_bytes(refined))
        evidence[0]["cacheSha256"] = recovery.content.file_hash(path)
        with self.assertRaisesRegex(recovery.content.ContentError, "retained measured recognizer output"):
            recovery.checked_scene_words(self.root, self.track, recording, baseline, evidence)

    def test_cache_only_rebuild_uses_separate_retained_root_without_inference(self):
        recording, baseline, _, result_asset, refined, _ = self.refinement_fixture()
        model = refined["cache_identity"]["sceneRefinement"]["modelIdentity"]
        retained, output = self.root / "retained-recognition", self.root / "final-clocks"
        result = recovery.content.read_json(self.root / result_asset["path"])
        request_asset = recovery.content.export_asset(retained, self.track["id"], "scene-recognition-request", result["identity"])
        alias = retained / "tracks" / self.track["id"] / ("scene-recognition-result." + request_asset["sha256"] + ".json")
        alias.write_bytes(recovery.content.json_bytes(result))
        with patch.object(recovery.audio, "refinement_windows", return_value={recording["selectedClips"][0]["id"]: [(5.0, 15.0)]}), \
                patch("builtins.print"), patch.object(recovery, "time") as clock:
            clock.monotonic.return_value = 0.0
            transcriber = unittest.mock.Mock(side_effect=AssertionError("cache-only rebuild must not infer"))
            evidence, _, count = recovery.refine_recording(self.root, output, self.track, recording, baseline,
                                  [self.target], model, transcriber, recognition_root=retained)
        transcriber.assert_not_called()
        self.assertEqual(count, 1)
        self.assertTrue((self.root / evidence[0]["sourcePath"]).is_file())
        self.assertIn("final-clocks/", evidence[0]["sourcePath"])
        # Original recognition bytes remain in their retained source directory.
        self.assertEqual(recovery.content.read_json(alias), result)


if __name__ == "__main__":
    unittest.main()
