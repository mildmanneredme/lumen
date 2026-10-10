"""Unchanged private web audio and independently measured narration clocks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web/scripts"))
import prepare_book_audio as audio
import book_content as content


class WebAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.track = content.make_track(1, "chapter", "The door opened. She left.", "v6")
        self.recording = {"trackId": self.track["id"], "narratorId": "autonoe", "sha256": "a" * 64,
                          "decodedDuration": 8.0, "sampleRate": 100, "decodedSamples": 800}
        self.qa = {"assembly": {"internal_silence_removed": False, "source_samples": 700,
                               "leading_source_samples_trimmed": 50, "trailing_source_samples_trimmed": 50,
                               "body_samples": 600, "added_head_samples": 100, "added_tail_samples": 100},
                   "coordinator_checks": {"sample_rate": 100, "expected_master_samples": 800,
                       "chunk_timeline": [{"id": "take-a", "source_start_seconds": 0.0,
                                           "source_end_seconds": 3.0, "master_start_seconds": 1.0,
                                           "master_end_seconds": 3.5, "retained_samples": 250},
                                          {"id": "take-b", "source_start_seconds": 3.0,
                                           "source_end_seconds": 7.0, "master_start_seconds": 3.5,
                                           "master_end_seconds": 7.0, "retained_samples": 350}]}}

    def test_outer_trim_and_padding_translate_each_raw_clock(self):
        self.assertEqual(audio.raw_to_master(self.qa, "take-a", .75), 1.25)
        self.assertEqual(audio.raw_to_master(self.qa, "take-b", .25), 3.75)
        for mutation in [lambda q: q["assembly"].update(internal_silence_removed=True),
                         lambda q: q["assembly"].update(body_samples=601),
                         lambda q: q["coordinator_checks"]["chunk_timeline"][1].update(master_start_seconds=4.0)]:
            changed = copy.deepcopy(self.qa)
            mutation(changed)
            with self.assertRaises(content.ContentError):
                audio.raw_to_master(changed, "take-a", 1.0)

    def test_sentence_times_come_from_matched_words_and_keep_voice_hash(self):
        words = [("The", 1.0, 1.2), ("door", 1.2, 1.6), ("opened.", 1.6, 2.1),
                 ("She", 4.0, 4.3), ("left.", 4.3, 4.8)]
        timing, report = audio.align_sentences(self.track, self.recording, words)
        self.assertEqual([(c["start"], c["end"]) for c in timing["sentences"]], [(1.0, 2.1), (4.0, 4.8)])
        self.assertEqual(timing["narratorId"], "autonoe")
        self.assertEqual(timing["audioSha256"], "a" * 64)
        self.assertNotIn("alignment", timing["approvals"])
        self.assertEqual(report["matchedTokens"], 5)

    def test_missing_sentence_has_no_invented_cue(self):
        timing, report = audio.align_sentences(self.track, self.recording,
                                             [("The", 1.0, 1.2), ("door", 1.2, 1.6), ("opened.", 1.6, 2.1)])
        self.assertIsNone(timing)
        self.assertEqual(report["unanchoredSentences"], [content.sentences(self.track)[1]["id"]])
        self.assertEqual([cue["sentenceId"] for cue in report["measuredSentences"]],
                         [content.sentences(self.track)[0]["id"]])

    def test_missing_sentence_with_overlapping_measured_neighbors_has_only_a_recheck_region(self):
        track = content.make_track(1, "chapter", "Before. Missing. After.", "v6")
        timing, report = audio.align_sentences(track, self.recording,
                                             [("Before.", 1.0, 2.0), ("After.", 1.8, 2.5)])
        missing_id = content.sentences(track)[1]["id"]
        self.assertIsNone(timing)
        self.assertEqual(report["unanchoredSentences"], [missing_id])
        region = report["unanchoredIntervals"][0]
        self.assertEqual((region["start"], region["end"]), (1.8, 2.0))
        self.assertEqual(region["neighboringMeasuredIntervals"], {"previousEnd": 2.0, "nextStart": 1.8})
        self.assertEqual(region["reason"], "overlapping-measured-neighbors")
        self.assertNotIn(missing_id, [cue["sentenceId"] for cue in report["measuredSentences"]])
        self.assertEqual(audio.refinement_windows(self.qa, [region], context=0), {"take-a": [(1.3, 1.5)]})

    def test_overlap_report_checks_against_all_preceding_measured_cues(self):
        track = content.make_track(1, "chapter", "Before. Brief. After.", "v6")
        timing, report = audio.align_sentences(track, self.recording,
                                             [("Before.", 1.0, 5.0), ("Brief.", 2.0, 3.0), ("After.", 4.0, 4.5)])
        self.assertIsNone(timing)
        self.assertEqual(report["overlappingSentences"], [s["id"] for s in content.sentences(track)[1:]])

    def test_spoken_numbers_brands_and_countdown_use_measured_word_spans(self):
        track = content.make_track(1, "story", "Twenty-six. NovaMind. T-08:00", "v6")
        words = [("26.", 1, 1.5), ("Nova", 2, 2.5), ("Mind.", 2.5, 3),
                 ("Eight", 4, 4.5), ("hours", 4.5, 5), ("remaining.", 5, 5.5)]
        timing, report = audio.align_sentences(track, self.recording, words)
        self.assertIsNotNone(timing)
        self.assertEqual([(cue["start"], cue["end"]) for cue in timing["sentences"]], [(1, 1.5), (2, 3), (4, 5.5)])
        self.assertEqual(report["matchedFraction"], 1.0)

    def test_contextual_asr_spelling_uses_actual_word_time_and_reports_substitution(self):
        track = content.make_track(1, "story", "He saw it. NovaMind. He knew it.", "v6")
        words = [("He", 1, 1.2), ("saw", 1.2, 1.5), ("it.", 1.5, 2),
                 ("Novamine.", 3, 3.6), ("He", 4, 4.2), ("knew", 4.2, 4.4), ("it.", 4.4, 4.8)]
        timing, report = audio.align_sentences(track, self.recording, words)
        self.assertIsNotNone(timing)
        self.assertEqual((timing["sentences"][1]["start"], timing["sentences"][1]["end"]), (3, 3.6))
        self.assertEqual(report["contextualSubstitutions"][0]["expected"], "novamind")
        self.assertEqual(report["contextualSubstitutions"][0]["heard"], "novamine")

    def test_unanchored_gaps_use_neighbor_measurements_for_local_recheck_only(self):
        track = content.make_track(1, "story", "The door opened. Quiet. She left.", "v6")
        words = [("The", 1, 1.2), ("door", 1.2, 1.5), ("opened.", 1.5, 2),
                 ("She", 4, 4.2), ("left.", 4.2, 4.8)]
        timing, report = audio.align_sentences(track, self.recording, words)
        self.assertIsNone(timing)
        self.assertEqual(report["unanchoredIntervals"][0]["start"], 2)
        self.assertEqual(report["unanchoredIntervals"][0]["end"], 4)
        windows = audio.refinement_windows(self.qa, report["unanchoredIntervals"], context=.1)
        self.assertEqual(windows["take-a"], [(1.4, 3.0)])
        self.assertEqual(windows["take-b"], [(0.0, .6)])

    def test_time_overlap_nonfinite_and_out_of_bounds_fail(self):
        for words in [[("The", 2, 3), ("door", 1, 2), ("opened", 2, 3), ("She", 4, 5), ("left", 5, 6)],
                      [("The", float("nan"), 3)], [("The", 8, 9)]]:
            with self.assertRaises(content.ContentError):
                audio.align_sentences(self.track, self.recording, words)

    def test_asr_cache_requires_exact_selected_audio_request_and_text(self):
        item = {"id": "take-a", "text": "The door opened.", "request_sha256": "b" * 64}
        clip = {"id": "take-a", "rawAudioSha256": "c" * 64}
        record = {"id": "take-a", "expected_inputtext": item["text"], "duration_seconds": 3.0,
                  "cache_identity": {"audio_sha256": "c" * 64, "request_sha256": "b" * 64,
                     "inputtext_sha256": hashlib.sha256(item["text"].encode()).hexdigest()},
                  "segments": [{"words": [{"word": "The", "start": .75, "end": 1.0}]}]}
        self.assertEqual(audio.cache_words(record, item, clip, self.qa)[0], ("The", 1.25, 1.5))
        for key in ["audio_sha256", "request_sha256", "inputtext_sha256"]:
            changed = copy.deepcopy(record)
            changed["cache_identity"][key] = "d" * 64
            with self.assertRaises(content.ContentError):
                audio.cache_words(changed, item, clip, self.qa)
        changed = copy.deepcopy(record)
        changed["expected_inputtext"] += " Stale."
        with self.assertRaises(content.ContentError):
            audio.cache_words(changed, item, clip, self.qa)

    def test_output_is_private_and_rejects_public_or_symlink_destinations(self):
        good = self.root / "Audiobook/author-audit/web-release"
        self.assertEqual(audio.private_destination(self.root, good), good.resolve())
        with self.assertRaises(content.ContentError):
            audio.private_destination(self.root, self.root / "web/dist")
        outside = self.root / "public"
        outside.mkdir()
        good.rmdir()
        good.parent.mkdir(parents=True, exist_ok=True)
        good.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(content.ContentError):
            audio.private_destination(self.root, good)

    def test_author_approval_is_bound_once_and_never_follows_changed_audio(self):
        destination = audio.private_destination(self.root, self.root / "Audiobook/author-audit/web-release")
        registry = {"registrySha256": "e" * 64}
        row = dict(self.recording, textSha256=self.track["textSha256"], selectionSha256="f" * 64)
        with self.assertRaises(content.ContentError):
            audio.author_approval(destination, registry, [row])
        approval = audio.author_approval(destination, registry, [row], initialize=True)
        self.assertEqual(approval["statements"][0], "Audio can go as is")
        self.assertEqual(audio.author_approval(destination, registry, [row]), approval)
        changed = dict(row, sha256="b" * 64)
        with self.assertRaises(content.ContentError):
            audio.author_approval(destination, registry, [changed], initialize=True)

    def test_private_pointer_rejects_file_symlink_and_keeps_target_unchanged(self):
        destination = audio.private_destination(self.root, self.root / "Audiobook/author-audit/web-release")
        target = self.root / "target.json"
        target.write_text("keep")
        (destination / "pointer.json").symlink_to(target)
        with self.assertRaises(content.ContentError):
            audio.write_pointer(destination, "pointer.json", {"new": True})
        self.assertEqual(target.read_text(), "keep")

    def test_refinement_keeps_measured_words_outside_the_rechecked_window(self):
        original = [{"word": "before", "start": 0, "end": .5},
                    {"word": "bad", "start": 1, "end": 2},
                    {"word": "after", "start": 3, "end": 4}]
        refined = [{"word": "good", "start": 1.1, "end": 1.7}]
        result = audio.merge_refinement_words(original, refined, [(1, 2)])
        self.assertEqual([word["word"] for word in result], ["before", "good", "after"])
        self.assertEqual(result[0], original[0])
        self.assertEqual(result[-1], original[-1])

    def private_alignment_fixture(self, root):
        """Tiny local evidence; inference is stubbed and no real audio is read."""
        destination = audio.private_destination(root, root / "Audiobook/author-audit/web-release")
        raw = root / "Audiobook/v7/chunk-a.wav"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"selected raw fixture")
        item = {"id": "take-a", "text": self.track["paragraphs"][0]["text"], "request_sha256": "b" * 64}
        (root / "Audiobook/v7/generation-manifest.json").write_text(json.dumps({"items": [item]}))
        qa_path = root / "Audiobook/v7/mastered/chapter-001.qa.json"
        qa_path.parent.mkdir()
        qa_path.write_text(json.dumps(self.qa))
        clip = {"id": "take-a", "rawAudioSha256": content.file_hash(raw),
                "sourcePath": str(raw.relative_to(root))}
        recording = dict(self.recording, productionEdition="v7", selectedClips=[clip],
                         sourcePath="Audiobook/v7/mastered/chapter-001.mp3",
                         sourceAudioPath="Audiobook/v7/mastered/chapter-001.mp3",
                         sourceAudioSha256=self.recording["sha256"])
        words = [{"word": word, "start": start, "end": end} for word, start, end in
                 [("The", .75, 1.0), ("door", 1.0, 1.2), ("opened.", 1.2, 1.5),
                  ("She", 2.0, 2.2), ("left.", 2.2, 2.5)]]
        base = {"id": "take-a", "expected_inputtext": item["text"], "duration_seconds": 3.0,
                "cache_identity": {"audio_sha256": clip["rawAudioSha256"],
                   "request_sha256": item["request_sha256"],
                   "inputtext_sha256": hashlib.sha256(item["text"].encode()).hexdigest()},
                "segments": [{"words": words}]}
        cache = root / "Audiobook/v7/local-checks/cache"
        cache.mkdir(parents=True)
        (cache / "take-a.base.json").write_text(json.dumps(base))
        model = root / "Audiobook/v6/asr-model-medium"
        model.mkdir(parents=True)
        fake = types.SimpleNamespace(transcribe=mock.Mock(return_value={"segments": [{"words": words}]}))
        return destination, recording, model, fake

    def test_refinement_rejects_every_private_cache_ancestor_symlink(self):
        for relative in ["alignment-cache", "alignment-cache/v7", "alignment-cache/v7/cache"]:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                destination, recording, model, fake = self.private_alignment_fixture(root)
                public = root / "web/dist"
                public.mkdir(parents=True)
                link = destination / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(public, target_is_directory=True)
                reports = [{"trackId": recording["trackId"], "narratorId": recording["narratorId"],
                            "unanchoredIntervals": [{"start": 1.5, "end": 2.0}]}]
                with mock.patch.object(audio.COMPARISON, "model_identity", return_value={"files_sha256": {"weights.safetensors": "f" * 64}}), \
                     mock.patch.dict(sys.modules, {"mlx_whisper": fake}):
                    with self.assertRaises(content.ContentError):
                        audio.refine_alignment_gaps(root, destination, {"recordings": [recording]}, reports, model)
                self.assertEqual(list(public.iterdir()), [])

    def test_timing_export_rejects_private_timings_symlink(self):
        destination, recording, _, _ = self.private_alignment_fixture(self.root)
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        (destination / "timings").symlink_to(public, target_is_directory=True)
        approval = {"reviewer": "fixture", "reviewedAt": "2026-10-10T00:00:00+00:00"}
        with self.assertRaises(content.ContentError):
            audio.build_timings(self.root.resolve(), destination, {"tracks": [self.track]}, [recording], approval)
        self.assertEqual(list(public.iterdir()), [])

    def test_transcription_rejects_changed_raw_selection_before_inference(self):
        destination = audio.private_destination(self.root, self.root / "Audiobook/author-audit/web-release")
        checker = self.root / "Audiobook/v8/check_audio_local.py"
        checker.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "Audiobook/v8/check_audio_local.py", checker)
        model = self.root / "Audiobook/v6/asr-model"
        model.mkdir(parents=True)
        (model / "config.json").write_text("{}")
        (model / "weights.safetensors").write_bytes(b"fixture")
        raw = self.root / "Audiobook/v7/take-a.wav"
        raw.parent.mkdir(parents=True)
        with wave.open(str(raw), "wb") as stream:
            stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\0\0" * 8000)
        item = {"id": "take-a", "text": "The door opened.", "request_sha256": "b" * 64}
        raw.with_suffix(".json").write_text(json.dumps({"request_sha256": item["request_sha256"],
                                                       "audio_sha256": content.file_hash(raw)}))
        (raw.parent / "generation-manifest.json").write_text(json.dumps({"items": [item]}))
        recording = {"trackId": "chapter-001", "narratorId": "autonoe", "productionEdition": "v7",
                     "selectedClips": [{"id": "take-a", "sourcePath": str(raw.relative_to(self.root)),
                                        "rawAudioSha256": "d" * 64}]}
        fake = types.SimpleNamespace(transcribe=mock.Mock(return_value={"text": "The door opened.", "segments": []}))
        with mock.patch("importlib.metadata.version", return_value="fixture"), \
             mock.patch.dict(sys.modules, {"mlx_whisper": fake}):
            with self.assertRaises(content.ContentError):
                audio.transcribe_missing(self.root, destination, {}, {"recordings": [recording]}, model)
        fake.transcribe.assert_not_called()

    def test_refinement_serializes_backend_float_scalars_before_strict_clock_checks(self):
        class BackendFloat(float):
            pass
        destination, recording, model, fake = self.private_alignment_fixture(self.root)
        for word in fake.transcribe.return_value["segments"][0]["words"]:
            word["start"] = BackendFloat(word["start"])
            word["end"] = BackendFloat(word["end"])
        reports = [{"trackId": recording["trackId"], "narratorId": recording["narratorId"],
                    "unanchoredIntervals": [{"start": 1.5, "end": 2.0}]}]
        with mock.patch.object(audio.COMPARISON, "model_identity", return_value={"files_sha256": {"weights.safetensors": "f" * 64}}), \
             mock.patch.dict(sys.modules, {"mlx_whisper": fake}):
            audio.refine_alignment_gaps(self.root.resolve(), destination, {"recordings": [recording]}, reports, model)
        manifest = content.read_json(self.root / "Audiobook/v7/generation-manifest.json")
        _, cached = audio.find_cache(self.root.resolve(), destination, recording, manifest["items"][0])
        self.assertTrue(cached.get("refinementAttempts"))
        self.assertIs(type(cached["segments"][0]["words"][0]["start"]), float)

    def test_refinement_preserves_baseline_for_invalid_window_and_continues_other_windows(self):
        for start, end in [(float("nan"), .9), (.9, .7), (.7, 4.0)]:
            with self.subTest(start=start, end=end), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                destination, recording, model, fake = self.private_alignment_fixture(root)
                fake.transcribe.side_effect = [
                    {"segments": [{"words": [{"word": "rejected", "start": start, "end": end}]}]},
                    {"segments": [{"words": [{"word": "She", "start": 2.0, "end": 2.2},
                                             {"word": "left.", "start": 2.2, "end": 2.5}]}]}]
                reports = [{"trackId": recording["trackId"], "narratorId": recording["narratorId"],
                            "unanchoredIntervals": [{"start": 1.5, "end": 2.0}]}]
                with mock.patch.object(audio.COMPARISON, "model_identity", return_value={"files_sha256": {"weights.safetensors": "f" * 64}}), \
                     mock.patch.object(audio, "refinement_windows", return_value={"take-a": [(0.0, 1.0), (2.0, 3.0)]}), \
                     mock.patch.dict(sys.modules, {"mlx_whisper": fake}):
                    audio.refine_alignment_gaps(root, destination, {"recordings": [recording]}, reports, model)
                item = content.read_json(root / "Audiobook/v7/generation-manifest.json")["items"][0]
                _, cached = audio.find_cache(root, destination, recording, item)
                self.assertEqual(fake.transcribe.call_count, 2)
                self.assertEqual([entry["status"] for entry in cached["refinementAttempts"]],
                                 ["rejected-invalid-clock", "measured"])
                self.assertEqual(cached["segments"][0]["words"][0], {"word": "The", "start": .75, "end": 1.0})
                self.assertNotIn("rejected", cached["transcript"])
                self.assertEqual(cached["cache_identity"]["refinement"]["rejectedWindows"][0]["reason"],
                                 "invalid-raw-ASR-word-clock")

    def test_only_one_local_gpu_alignment_worker_can_run(self):
        destination = audio.private_destination(self.root, self.root / "Audiobook/author-audit/web-release")
        with audio.gpu_worker_lock(destination):
            with self.assertRaises(content.ContentError):
                with audio.gpu_worker_lock(destination):
                    self.fail("A second GPU worker must not enter")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "local ffmpeg is required")
    def test_existing_192kbps_clock_is_measured_without_reencoding(self):
        target = self.root / "existing.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.25",
                        "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k", str(target)], check=True)
        digest = content.file_hash(target)
        measured = audio.measure_audio(target)
        self.assertEqual(measured["bitRate"], 192000)
        self.assertEqual(measured["decodedSamples"], 55125)
        self.assertAlmostEqual(measured["decodedDuration"], 1.25, places=6)
        self.assertLess(measured["samplePeakDbfs"], 0)
        self.assertEqual(content.file_hash(target), digest)


if __name__ == "__main__":
    unittest.main()
