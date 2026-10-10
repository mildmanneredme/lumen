"""Release integrity regressions: these gates run before public assets exist."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("book_content", ROOT / "web/scripts/book_content.py")
content = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(content)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ContentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "v6"
        self.source.mkdir()
        self.source_text = "# LUMEN\n\n## Chapter 1\n\nDr. Vale waited. *Why now?*\n\n---\n\n**Later.** She paid 4.2 dollars.\n\n---\n\n## Chapter 2\n\nA different ending.\n"
        (self.source / "part1.md").write_text(self.source_text)
        self.manifest = {
            "voice": "Charon", "source_sha256": {"part1.md": digest(self.source_text)},
            "items": [
                {"id": "chapter-000-001", "chapter": 0, "text": "Lumen. Written by Rob Xie."},
                {"id": "chapter-001-001", "chapter": 1,
                 "text": "Chapter One.\n\nDr. Vale waited. Why now?\n\n<short pause>\n\nLater. She paid 4.2 dollars."},
                {"id": "chapter-002-001", "chapter": 2, "text": "Chapter Two.\n\nA different ending."},
                {"id": "chapter-003-001", "chapter": 3, "text": "The End."},
            ],
        }
        self.other = copy.deepcopy(self.manifest)
        self.other["voice"] = "Autonoe"

    def registry(self, **kwargs):
        return content.build_registry(self.source, [self.manifest, self.other], expected_chapters=2, **kwargs)

    def timing_fixture(self):
        registry = self.registry()
        track = registry["tracks"][1]
        recording = {
            "trackId": track["id"], "narratorId": "charon", "sha256": "a" * 64,
            "bytes": 128, "decodedDuration": 8.0, "audioHashVerified": True,
        }
        cues = [{"sentenceId": sentence["id"], "start": index + 1.0, "end": index + 1.6}
                for index, sentence in enumerate(content.sentences(track))]
        timing = {
            "schemaVersion": 1, "trackId": track["id"], "manuscriptVersion": "v6",
            "textSha256": track["textSha256"], "narratorId": "charon",
            "audioSha256": recording["sha256"], "duration": 8.0, "sentences": cues,
            "coverage": {"introEnd": cues[0]["start"], "tailStart": cues[-1]["end"]},
            "approvals": {},
        }
        return track, recording, timing

    def test_display_prose_emphasis_scene_breaks_and_sentence_offsets_are_lossless(self):
        registry = self.registry()
        track = registry["tracks"][1]
        self.assertEqual(track["bodyMarkdown"], "Dr. Vale waited. *Why now?*\n\n---\n\n**Later.** She paid 4.2 dollars.")
        self.assertEqual([b["kind"] for b in track["blocks"]], ["paragraph", "scene-break", "paragraph"])
        self.assertEqual([s["text"] for s in content.sentences(track)],
                         ["Dr. Vale waited.", "Why now?", "Later.", "She paid 4.2 dollars."])
        paragraphs = track["paragraphs"]
        self.assertEqual(paragraphs[0]["emphasis"], [{"start": 17, "end": 25, "kind": "italic"}])
        self.assertEqual(paragraphs[1]["emphasis"], [{"start": 0, "end": 6, "kind": "bold"}])
        content.validate_registry(registry)

    def test_track_types_order_and_qualified_sentence_ids(self):
        registry = self.registry()
        self.assertEqual([t["id"] for t in registry["tracks"]], [f"chapter-{i:03d}" for i in range(4)])
        self.assertEqual([t["kind"] for t in registry["tracks"]],
                         ["opening-credits", "story", "story", "closing-credits"])
        ids = [s["id"] for t in registry["tracks"] for s in content.sentences(t)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("v6:chapter-001:p001-s01", ids)
        self.assertIn("v6:chapter-002:p001-s01", ids)
        self.assertTrue(all(t["publicationStatus"] == "pending" for t in registry["tracks"]))

    def test_initialisms_and_name_initials_do_not_split_a_sentence(self):
        cases = [
            ("He called from his D.C. residence. Nobody answered.",
             ["He called from his D.C. residence.", "Nobody answered."]),
            ("A U.S. senator spoke. She left.", ["A U.S. senator spoke.", "She left."]),
            ("The U.S. Supreme Court convened. They waited.",
             ["The U.S. Supreme Court convened.", "They waited."]),
            ("N.A.T.O. officials arrived. Nothing changed.", ["N.A.T.O. officials arrived.", "Nothing changed."]),
            ("J. R. Oppenheimer waited. It was late.", ["J. R. Oppenheimer waited.", "It was late."]),
            ("He knew J. Smith. Smith disagreed.", ["He knew J. Smith.", "Smith disagreed."]),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual([text[start:end] for start, end in content.sentence_ranges(text)], expected)

    def test_initialism_period_still_ends_sentence_at_a_clear_new_sentence(self):
        cases = [
            ("They arrived in D.C. He made a call.", ["They arrived in D.C.", "He made a call."]),
            ("She lived in the U.S. The hearing started.", ["She lived in the U.S.", "The hearing started."]),
            ("Washington, D.C.", ["Washington, D.C."]),
            ('"He lives in D.C." She waited.', ['"He lives in D.C."', "She waited."]),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual([text[start:end] for start, end in content.sentence_ranges(text)], expected)

    def test_changed_source_hash_is_rejected(self):
        (self.source / "part1.md").write_text(self.source_text.replace("waited", "left"))
        with self.assertRaisesRegex(content.ContentError, "source hash"):
            self.registry()

    def test_mismatched_voice_wording_is_rejected(self):
        self.other["items"][1]["text"] = self.other["items"][1]["text"].replace("Why now", "Why later")
        with self.assertRaisesRegex(content.ContentError, "narration differs"):
            self.registry()

    def test_generation_cannot_omit_displayed_words(self):
        for manifest in [self.manifest, self.other]:
            manifest["items"][1]["text"] = manifest["items"][1]["text"].replace("Dr. Vale waited. ", "")
        with self.assertRaisesRegex(content.ContentError, "canonical prose"):
            self.registry()

    def test_same_version_anchor_cannot_silently_change_prose(self):
        previous = self.registry()
        altered = self.source_text.replace("waited", "stayed")
        (self.source / "part1.md").write_text(altered)
        for manifest in [self.manifest, self.other]:
            manifest["source_sha256"]["part1.md"] = digest(altered)
            manifest["items"][1]["text"] = manifest["items"][1]["text"].replace("waited", "stayed")
        with self.assertRaisesRegex(content.ContentError, "anchor reuse"):
            self.registry(previous=previous)

    def test_pilot_aliases_require_matching_prose_and_preserve_excerpt_endpoint(self):
        pilot = {"id": "chapter-001-pilot", "chapterId": "chapter-001", "manuscriptVersion": "v6",
                 "paragraphs": [{"id": "p001", "text": "Dr. Vale waited. Why now?", "sentences": [
                     {"id": "p001-s01", "text": "Dr. Vale waited."},
                     {"id": "p001-s02", "text": "Why now?"}]}]}
        aliases = self.registry(pilot=pilot)["legacyAliases"]["chapter-001-pilot"]
        self.assertEqual(aliases["sentenceIds"]["p001-s01"], "v6:chapter-001:p001-s01")
        self.assertEqual(aliases["completedExcerpt"], {"sentenceId": "v6:chapter-001:p001-s02", "sentenceFraction": 1.0,
                                                       "trackCompleted": False})
        pilot["paragraphs"][0]["sentences"][0]["text"] = "Wrong wording."
        with self.assertRaisesRegex(content.ContentError, "[Pp]ilot"):
            self.registry(pilot=pilot)

    def test_timing_requires_final_audio_hash_voice_and_canonical_text(self):
        track, recording, timing = self.timing_fixture()
        content.validate_timing_map(track, recording, timing)
        for field, wrong in [("audioSha256", "b" * 64), ("narratorId", "autonoe"),
                             ("textSha256", "c" * 64), ("trackId", "chapter-002")]:
            with self.subTest(field=field):
                changed = copy.deepcopy(timing)
                changed[field] = wrong
                with self.assertRaises(content.ContentError):
                    content.validate_timing_map(track, recording, changed)

    def test_timing_rejects_missing_duplicate_unknown_overlapping_or_unbounded_cues(self):
        track, recording, timing = self.timing_fixture()
        variants = []
        missing = copy.deepcopy(timing); missing["sentences"].pop(); variants.append(missing)
        duplicate = copy.deepcopy(timing); duplicate["sentences"][1]["sentenceId"] = duplicate["sentences"][0]["sentenceId"]; variants.append(duplicate)
        unknown = copy.deepcopy(timing); unknown["sentences"][0]["sentenceId"] = "fake"; variants.append(unknown)
        overlap = copy.deepcopy(timing); overlap["sentences"][1]["start"] = 1.5; variants.append(overlap)
        beyond = copy.deepcopy(timing); beyond["sentences"][-1]["end"] = 9; variants.append(beyond)
        nonfinite = copy.deepcopy(timing); nonfinite["sentences"][0]["end"] = float("nan"); variants.append(nonfinite)
        for changed in variants:
            with self.assertRaises(content.ContentError):
                content.validate_timing_map(track, recording, changed)

    def test_timing_coverage_must_account_for_intro_and_tail(self):
        track, recording, timing = self.timing_fixture()
        for coverage in [{}, {"introEnd": 0.5, "tailStart": 7.0}]:
            timing["coverage"] = coverage
            with self.assertRaisesRegex(content.ContentError, "coverage"):
                content.validate_timing_map(track, recording, timing)

    def test_readiness_requires_human_approvals_bound_to_audio_and_text(self):
        track, recording, timing = self.timing_fixture()
        with self.assertRaisesRegex(content.ContentError, "approval"):
            content.validate_timing_map(track, recording, timing, require_approved=True)
        for name in ["content", "alignment"]:
            timing["approvals"][name] = {"status": "approved", "reviewer": "Rob Xie",
                "reviewedAt": "2026-10-10T12:00:00Z", "audioSha256": recording["sha256"],
                "textSha256": track["textSha256"]}
        timing["approvals"]["alignment"]["timingSha256"] = content.object_hash(
            {key: value for key, value in timing.items() if key != "approvals"})
        content.validate_timing_map(track, recording, timing, require_approved=True)
        recording["audioHashVerified"] = False
        with self.assertRaisesRegex(content.ContentError, "verified"):
            content.validate_timing_map(track, recording, timing, require_approved=True)

    def test_alignment_approval_is_invalidated_by_any_timing_content_change(self):
        track, recording, timing = self.timing_fixture()
        approved = {"status": "approved", "reviewer": "Rob Xie", "reviewedAt": "2026-10-10T12:00:00Z",
                    "audioSha256": recording["sha256"], "textSha256": track["textSha256"]}
        timing["approvals"] = {"content": dict(approved), "alignment": dict(approved)}
        timing["approvals"]["alignment"]["timingSha256"] = content.object_hash(
            {key: value for key, value in timing.items() if key != "approvals"})
        content.validate_timing_map(track, recording, timing, require_approved=True)
        variants = []
        cue = copy.deepcopy(timing); cue["sentences"][1]["start"] += 0.1; variants.append(cue)
        intro = copy.deepcopy(timing); intro["sentences"][0]["start"] += 0.1; intro["coverage"]["introEnd"] += 0.1; variants.append(intro)
        tail = copy.deepcopy(timing); tail["sentences"][-1]["end"] += 0.1; tail["coverage"]["tailStart"] += 0.1; variants.append(tail)
        extra_clock = copy.deepcopy(timing); extra_clock["encoderClockOffset"] = 0.05; variants.append(extra_clock)
        missing = copy.deepcopy(timing); missing["approvals"]["alignment"].pop("timingSha256"); variants.append(missing)
        for changed in variants:
            with self.subTest(change=changed):
                content.validate_timing_map(track, recording, changed)
                with self.assertRaisesRegex(content.ContentError, "alignment.*hash"):
                    content.validate_timing_map(track, recording, changed, require_approved=True)
        timing["approvals"]["alignment"]["reviewedAt"] = "2026-10-10T12:05:00Z"
        timing["approvals"]["alignment"]["note"] = "Reviewer metadata may change without changing the reviewed clock."
        content.validate_timing_map(track, recording, timing, require_approved=True)

    def test_scene_triggers_resolve_independently_for_each_voice(self):
        track, recording, timing = self.timing_fixture()
        scene = {"id": "waiting", "sentenceId": timing["sentences"][1]["sentenceId"], "src": "assets/waiting.webp"}
        self.assertEqual(content.resolve_scenes(track, recording, timing, [scene])[0]["start"], 2.0)
        other_recording = dict(recording, narratorId="autonoe", sha256="d" * 64, decodedDuration=16.0)
        other_timing = copy.deepcopy(timing)
        other_timing.update(narratorId="autonoe", audioSha256="d" * 64, duration=16.0)
        for cue in other_timing["sentences"]:
            cue["start"] *= 2; cue["end"] *= 2
        other_timing["coverage"] = {"introEnd": 2.0, "tailStart": 9.2}
        self.assertEqual(content.resolve_scenes(track, other_recording, other_timing, [scene])[0]["start"], 4.0)
        scene["sentenceId"] = "unknown"
        with self.assertRaisesRegex(content.ContentError, "scene"):
            content.resolve_scenes(track, recording, timing, [scene])

    def test_content_addressed_export_is_idempotent_and_never_overwrites_collision(self):
        destination = Path(self.temp.name) / "staging"
        value = self.registry()["tracks"][1]
        first = content.export_asset(destination, "chapter-001", "text", value)
        path = destination / first["path"]
        timestamp = path.stat().st_mtime_ns
        second = content.export_asset(destination, "chapter-001", "text", value)
        self.assertEqual(first, second)
        self.assertEqual(path.stat().st_mtime_ns, timestamp)
        self.assertEqual(json.loads(path.read_text()), value)
        path.write_text("collision")
        with self.assertRaisesRegex(content.ContentError, "immutable"):
            content.export_asset(destination, "chapter-001", "text", value)

    def test_export_rejects_unsafe_ids(self):
        with self.assertRaises(content.ContentError):
            content.export_asset(Path(self.temp.name), "../../other", "text", {})

    def test_export_rejects_staging_symlink_escape_before_creating_public_directories(self):
        root = Path(self.temp.name)
        public = root / "web/dist"
        public.mkdir(parents=True)
        for nesting in ["tracks", "tracks/chapter-001"]:
            staging = root / ("staging-" + nesting.replace("/", "-"))
            link = staging / nesting
            link.parent.mkdir(parents=True)
            link.symlink_to(public, target_is_directory=True)
            with self.subTest(nesting=nesting):
                with self.assertRaisesRegex(content.ContentError, "containment|symlink"):
                    content.export_asset(staging, "chapter-001", "text", self.registry()["tracks"][1])
                self.assertEqual(list(public.rglob("*")), [], "No private file or directory may be created in public dist")

    def test_import_rejects_unknown_blocks_unqualified_ids_or_changed_registry_hash(self):
        original = self.registry()
        variants = []
        unknown = copy.deepcopy(original); unknown["tracks"][1]["blocks"][0]["paragraphId"] = "fake"; variants.append(unknown)
        unqualified = copy.deepcopy(original); unqualified["tracks"][1]["paragraphs"][0]["id"] = "p001"; variants.append(unqualified)
        changed = copy.deepcopy(original); changed["tracks"][1]["title"] = "Different title"; variants.append(changed)
        for registry in variants:
            with self.assertRaises(content.ContentError):
                content.validate_registry(registry)

    def test_missing_narration_heading_or_extra_instruction_cannot_pass(self):
        for phrase in ["", "Read this slowly. Chapter One."]:
            manifests = [copy.deepcopy(self.manifest), copy.deepcopy(self.other)]
            for manifest in manifests:
                manifest["items"][1]["text"] = manifest["items"][1]["text"].replace("Chapter One.", phrase)
            with self.assertRaises(content.ContentError):
                content.build_registry(self.source, manifests, expected_chapters=2)

    def test_invalid_shape_timing_fails_as_contract_error(self):
        track, recording, timing = self.timing_fixture()
        for cues in [[], ["bad"], [None]]:
            timing["sentences"] = cues
            with self.assertRaises(content.ContentError):
                content.validate_timing_map(track, recording, timing)

    def test_readiness_rejects_stale_hashes_anonymous_or_undated_approval(self):
        track, recording, timing = self.timing_fixture()
        approved = {"status": "approved", "reviewer": "Rob Xie", "reviewedAt": "2026-10-10T12:00:00Z",
                    "audioSha256": recording["sha256"], "textSha256": track["textSha256"]}
        for change in [{"audioSha256": "b" * 64}, {"reviewer": ""}, {"reviewedAt": "2026-10-10"},
                       {"status": "accepted-with-note", "note": ""}]:
            timing["approvals"] = {"content": dict(approved, **change), "alignment": approved}
            with self.assertRaisesRegex(content.ContentError, "approval"):
                content.validate_timing_map(track, recording, timing, require_approved=True)

    def test_recording_verification_hashes_bytes_without_approving_content(self):
        root = Path(self.temp.name)
        audio = root / "audio.mp3"
        audio.write_bytes(b"encoded media fixture")
        recording = {"sourcePath": "audio.mp3", "bytes": audio.stat().st_size,
                     "sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                     "audioHashVerified": False, "publicationStatus": "pending"}
        verified = content.verify_recording_file(recording, root)
        self.assertTrue(verified["audioHashVerified"])
        self.assertEqual(verified["publicationStatus"], "pending")
        self.assertFalse(recording["audioHashVerified"])
        audio.write_bytes(b"different bytes!!!!!")
        with self.assertRaises(content.ContentError):
            content.verify_recording_file(recording, root)


class ActualInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "Audiobook/v7/generation-manifest.json").is_file():
            raise unittest.SkipTest("Local bulk production metadata is unavailable")
        cls.registry = content.load_project_registry(ROOT)

    def test_full_canonical_registry_and_pilot_aliases(self):
        self.assertEqual(len(self.registry["tracks"]), 91)
        self.assertEqual(len(self.registry["toc"]), 89)
        self.assertEqual(set(self.registry["sourceHashes"]), {f"part{i}.md" for i in range(1, 6)})
        aliases = self.registry["legacyAliases"]["chapter-001-pilot"]
        self.assertEqual(len(aliases["sentenceIds"]), 60)
        self.assertFalse(aliases["completedExcerpt"]["trackCompleted"])
        content.validate_registry(self.registry)

    def test_both_recordings_are_independent_and_pending_human_approval(self):
        inventory = content.load_recording_inventory(ROOT, self.registry)
        self.assertEqual(len(inventory["recordings"]), 182)
        by_track = [r for r in inventory["recordings"] if r["trackId"] == "chapter-001"]
        self.assertEqual({r["narratorId"] for r in by_track}, {"charon", "autonoe"})
        self.assertNotEqual(by_track[0]["sha256"], by_track[1]["sha256"])
        self.assertNotEqual(by_track[0]["decodedDuration"], by_track[1]["decodedDuration"])
        self.assertTrue(all(r["publicationStatus"] == "pending" and r["audioHashVerified"] is False
                            for r in inventory["recordings"]))

    def test_chapter_50_d_c_residence_is_one_canonical_sentence(self):
        chapter = next(track for track in self.registry["tracks"] if track["id"] == "chapter-050")
        paragraph = next(paragraph for paragraph in chapter["paragraphs"] if "his D.C. residence" in paragraph["text"])
        self.assertEqual([sentence["text"] for sentence in paragraph["sentences"]], [
            "At 6:31 that morning in Washington, Polk opened the emergency video session from his D.C. residence.",
            "Screens showed damage assessments.", "Phones rang constantly.",
            "Suspicion crystallized quickly: someone with insider knowledge of all six companies had designed these exploits.",
        ])


if __name__ == "__main__":
    unittest.main()
