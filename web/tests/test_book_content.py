"""Release integrity regressions: these gates run before public assets exist."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
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

    def production_fixture(self):
        """Small provenance-bound fixture retaining historical absolute paths."""
        root = Path(self.temp.name) / "original-project"
        registry = self.registry()
        def write_json(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, sort_keys=True) + "\n")
        for edition, manifest in [("v7", self.other), ("v8", self.manifest)]:
            job = root / "Audiobook" / edition
            manifest_path = job / "generation-manifest.json"
            chapters_path = job / "chapters.json"
            report_path = job / "mastered/mastering-report.json"
            write_json(manifest_path, manifest)
            write_json(chapters_path, {f"{index:03d}": {"title": track["title"]}
                                       for index, track in enumerate(registry["tracks"])})
            write_json(report_path, {"complete": True})
            rows = []
            for index, track in enumerate(registry["tracks"]):
                audio = job / "mastered" / (track["id"] + ".mp3")
                audio.write_bytes((edition + track["id"]).encode())
                qa_path = audio.with_suffix(".qa.json")
                write_json(qa_path, {"decoded_mp3_qa": {"samples": 441000},
                                     "coordinator_checks": {"sample_rate": 44100}, "warnings": []})
                outputs = {"mp3": content.file_hash(audio), "qa": content.file_hash(qa_path)}
                checkpoint_path = audio.with_suffix(".checkpoint.json")
                write_json(checkpoint_path, {"output_sha256": outputs})
                rows.append({"key": f"{index:03d}", "file": str(audio), "bytes": audio.stat().st_size,
                             "sha256": outputs["mp3"], "source_binding": {
                                 "output_sha256": outputs, "checkpoint_sha256": content.file_hash(checkpoint_path)}})
            write_json(job / "delivery/delivery-manifest.json", {
                "complete": True, "generation_manifest_sha256": content.file_hash(manifest_path),
                "input_list_sha256": content.file_hash(chapters_path),
                "mastering_report_sha256": content.file_hash(report_path), "chapters": rows,
                "chapter_count": len(rows)})
        return root, registry

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

    def test_time_abbreviations_end_sentences_before_clear_openers(self):
        cases = [
            ("It was 11:47 a.m. A manila envelope arrived.", ["It was 11:47 a.m.", "A manila envelope arrived."]),
            ("It was 11:47 a.m. Her phone remained on.", ["It was 11:47 a.m.", "Her phone remained on."]),
            ("It was 12:17 p.m. It was an estimate, not a promise.",
             ["It was 12:17 p.m.", "It was an estimate, not a promise."]),
            ("It was 4:23 p.m. The crowd surged.", ["It was 4:23 p.m.", "The crowd surged."]),
            ("It was 7:38 a.m. Raven followed.", ["It was 7:38 a.m.", "Raven followed."]),
            ("It was 12:17 p.m. Thirty-five hours from now.", ["It was 12:17 p.m.", "Thirty-five hours from now."]),
            ("He left at 8:17 a.m. Santos rode beside him.", ["He left at 8:17 a.m.", "Santos rode beside him."]),
            ("The phone rang at 8:17 a.m. Tomás spoke.", ["The phone rang at 8:17 a.m.", "Tomás spoke."]),
            ("It was 11:47 a.m.", ["It was 11:47 a.m."]),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual([text[start:end] for start, end in content.sentence_ranges(text)], expected)

    def test_timezones_and_explanatory_abbreviations_continue_within_sentence(self):
        cases = [
            ("It was 11:47 a.m. London time when she called. Nobody answered.",
             ["It was 11:47 a.m. London time when she called.", "Nobody answered."]),
            ("At 12:17 p.m. Central European Time, he waited. They left.",
             ["At 12:17 p.m. Central European Time, he waited.", "They left."]),
            ("At 11:47 a.m. CET, the call started. It ended.",
             ["At 11:47 a.m. CET, the call started.", "It ended."]),
            ("They met at 3:47 a.m. Greenwich Mean Time. Then they waited.",
             ["They met at 3:47 a.m. Greenwich Mean Time.", "Then they waited."]),
            ("At 9:17 a.m. Berlin time she published it. It was ready.",
             ["At 9:17 a.m. Berlin time she published it.", "It was ready."]),
            ("It was 5:17 a.m. Eastern when they convened. He answered.",
             ["It was 5:17 a.m. Eastern when they convened.", "He answered."]),
            ("Use examples, e.g. the opening scene. Then review them.",
             ["Use examples, e.g. the opening scene.", "Then review them."]),
            ("Use the same voice, i.e. Charon throughout. They agreed.",
             ["Use the same voice, i.e. Charon throughout.", "They agreed."]),
            ("Phones, cameras, etc. were present. Nothing changed.",
             ["Phones, cameras, etc. were present.", "Nothing changed."]),
            ("He cited Marsh, et al. in the report. It was public.",
             ["He cited Marsh, et al. in the report.", "It was public."]),
            ("Phones, cameras, etc. He counted them.", ["Phones, cameras, etc.", "He counted them."]),
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

    def test_relocated_production_inventory_retains_provenance_and_verifies_local_bytes(self):
        original, registry = self.production_fixture()
        original_hashes = {str(path.relative_to(original)): content.file_hash(path)
                           for path in original.rglob("*.json")}
        relocated = Path(self.temp.name) / "relocated-project"
        shutil.copytree(original, relocated)
        shutil.rmtree(original)
        inventory = content.load_recording_inventory(relocated, registry)
        self.assertEqual(len(inventory["recordings"]), 8)
        for recording in inventory["recordings"]:
            self.assertTrue(content.verify_recording_file(recording, relocated)["audioHashVerified"])
            self.assertEqual(recording["sourcePath"],
                             f"Audiobook/{recording['productionEdition']}/mastered/{recording['trackId']}.mp3")
        self.assertEqual(original_hashes, {str(path.relative_to(relocated)): content.file_hash(path)
                                          for path in relocated.rglob("*.json")})

    def test_recording_inventory_rejects_wrong_logical_edition_filename_or_traversal(self):
        root, registry = self.production_fixture()
        path = root / "Audiobook/v7/delivery/delivery-manifest.json"
        original = json.loads(path.read_text())
        variants = [
            "/historical/Audiobook/v8/mastered/chapter-000.mp3",
            "/historical/Audiobook/v7/mastered/chapter-001.mp3",
            "/historical/Audiobook/v7/mastered/../mastered/chapter-000.mp3",
            "../../Audiobook/v7/mastered/chapter-000.mp3",
        ]
        for stored_path in variants:
            with self.subTest(stored_path=stored_path):
                changed = copy.deepcopy(original)
                changed["chapters"][0]["file"] = stored_path
                path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(content.ContentError, "path"):
                    content.load_recording_inventory(root, registry)

    def test_relocated_audio_verification_rejects_same_size_changed_bytes(self):
        original, registry = self.production_fixture()
        relocated = Path(self.temp.name) / "relocated-project"
        shutil.copytree(original, relocated)
        shutil.rmtree(original)
        inventory = content.load_recording_inventory(relocated, registry)
        recording = inventory["recordings"][0]
        target = relocated / recording["sourcePath"]
        target.write_bytes(b"x" * recording["bytes"])
        with self.assertRaisesRegex(content.ContentError, "hash"):
            content.verify_recording_file(recording, relocated)

    def test_recording_inventory_rejects_audio_symlink_outside_caller_root(self):
        root, registry = self.production_fixture()
        target = root / "Audiobook/v7/mastered/chapter-000.mp3"
        outside = Path(self.temp.name) / "outside.mp3"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaisesRegex(content.ContentError, "path escapes"):
            content.load_recording_inventory(root, registry)


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

    def test_actual_time_abbreviation_sentence_ends_remain_separate(self):
        cases = [
            ("chapter-006", "7:38 a.m.", "Raven followed"),
            ("chapter-014", "11:47 a.m.", "A manila envelope"),
            ("chapter-048", "11:47 a.m.", "Her phone remained on."),
            ("chapter-049", "12:17 p.m.", "It was an estimate, not a promise."),
            ("chapter-058", "12:17 p.m.", "Thirty-five hours from now."),
            ("chapter-070", "8:17 a.m.", "Santos rode beside him"),
            ("chapter-077", "7:31 p.m.", "In four minutes"),
            ("chapter-079", "8:17 a.m.", "Tomás's voice"),
            ("chapter-085", "nine a.m.", "The booth in the back."),
            ("chapter-089", "4:23 p.m.", "The crowd surged."),
        ]
        for track_id, abbreviation, opener in cases:
            track = next(track for track in self.registry["tracks"] if track["id"] == track_id)
            paragraph = next(paragraph for paragraph in track["paragraphs"]
                             if abbreviation + " " + opener in paragraph["text"])
            texts = [sentence["text"] for sentence in paragraph["sentences"]]
            with self.subTest(track=track_id):
                self.assertTrue(any(text.endswith(abbreviation) for text in texts))
                self.assertTrue(any(text.startswith(opener) for text in texts))

    def test_every_actual_time_abbreviation_preserves_timezone_continuations(self):
        continuations = ["Greenwich Mean Time", "London time", "Central European Time", "CET",
                         "Berlin time", "Tallinn time", "Lisbon time", "Austin time", "Pacific time",
                         "Brussels time", "Eastern"]
        for continuation in continuations:
            observed = 0
            for track in self.registry["tracks"]:
                for paragraph in track["paragraphs"]:
                    for abbreviation in ["a.m.", "p.m."]:
                        phrase = abbreviation + " " + continuation
                        if phrase in paragraph["text"]:
                            observed += 1
                            with self.subTest(track=track["id"], phrase=phrase):
                                self.assertTrue(any(phrase in sentence["text"] for sentence in paragraph["sentences"]))
            self.assertGreater(observed, 0, f"Production-source coverage is missing {continuation}")


if __name__ == "__main__":
    unittest.main()
