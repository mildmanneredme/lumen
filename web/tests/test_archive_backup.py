"""Compressed backups preserve original bytes without a second full copy."""
from __future__ import annotations

import copy
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch
import zipfile


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("archive_backup", SCRIPTS / "archive_backup.py")
archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archive)


class ArchiveBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.paths = ["Draft/v6/part1.md", "Audiobook/v8/raw/chapter-001-001.wav",
                      "Audiobook/v8/mastered/chapter-001.wav", "Audiobook/v8/mastered/chapter-001.mp3"]
        self.original = {}
        for index, name in enumerate(self.paths):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            self.original[name] = (f"fixture {index} speech silence " * 1000).encode()
            path.write_bytes(self.original[name])
        self.destination = self.base / "drive" / "Lumen" / "lumen-backup-fixture.zip"
        self.destination.parent.mkdir(parents=True)

    def plan(self, **kwargs):
        return archive.backup.prepare_fileset(self.root, self.paths, "lumen-backup-fixture", **kwargs)

    def create(self, plan=None, **kwargs):
        return archive.archive_snapshot(self.root, self.destination, plan or self.plan(),
                                        maximum_archive_bytes=1_000_000, minimum_free_bytes=0, **kwargs)

    @contextmanager
    def sidecar_fault(self, mode):
        original_fdopen, original_fsync, original_open = archive.os.fdopen, archive.os.fsync, archive.os.open
        checksum = archive.sidecar_path(self.destination)
        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                result = self.stream.__exit__(*args)
                if mode == "close":
                    raise OSError("fixture sidecar close failure after full write")
                return result
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def write(self, data):
                if mode == "partial":
                    self.stream.write(data[:len(data) // 2]); self.stream.flush()
                    raise OSError("fixture partial sidecar write")
                if mode == "malformed":
                    self.stream.write(b"x" * len(data)); self.stream.flush()
                    raise OSError("fixture malformed sidecar write")
                if mode == "different-json":
                    value = json.loads(data)
                    value["planSha256"] = ("a" if value["planSha256"][0] != "a" else "b") + value["planSha256"][1:]
                    self.stream.write(archive.backup.encoded(value)); self.stream.flush()
                    raise OSError("fixture different but parseable sidecar write")
                if mode == "short":
                    return self.stream.write(data[:len(data) // 2])
                if mode == "before-write":
                    raise OSError("fixture error before any sidecar bytes")
                return self.stream.write(data)
        def failed_fdopen(descriptor, file_mode, *args, **kwargs):
            stream = original_fdopen(descriptor, file_mode, *args, **kwargs)
            return FailingWriter(stream) if file_mode == "wb" else stream
        def failed_fsync(descriptor):
            if mode == "fsync" and checksum.exists() and os.fstat(descriptor).st_ino == checksum.stat().st_ino:
                raise OSError("fixture sidecar fsync failure after full write")
            return original_fsync(descriptor)
        def failed_open(name, flags, *args, **kwargs):
            if mode == "before-create" and name == checksum.name and flags & os.O_CREAT:
                raise OSError("fixture error before exclusive sidecar creation")
            return original_open(name, flags, *args, **kwargs)
        with patch.object(archive.os, "fdopen", side_effect=failed_fdopen), \
             patch.object(archive.os, "fsync", side_effect=failed_fsync), \
             patch.object(archive.os, "open", side_effect=failed_open):
            yield

    def test_post_write_sidecar_errors_return_completion_matching_current_byte_verifier(self):
        old_report = self.create()
        older = self.destination
        original_zip, original_sidecar = older.read_bytes(), archive.sidecar_path(older).read_bytes()
        for mode in ["fsync", "close"]:
            with self.subTest(mode=mode):
                self.destination = self.base / mode / older.name
                self.destination.parent.mkdir()
                with self.sidecar_fault(mode):
                    report = self.create()
                proof = archive.verify_archive(self.destination)
                self.assertEqual(report["copyStatus"], "complete")
                self.assertEqual(proof["verifiedFiles"], len(self.paths))
                self.assertEqual(report["manifestSha256"], proof["manifestSha256"])
                self.assertEqual(report["completionPublication"]["status"], "exact-readback-after-write-error")
                self.assertFalse(report["completionPublication"]["durabilityVerified"])
                marker = json.loads(archive.sidecar_path(self.destination).read_bytes())
                self.assertEqual({key: value for key, value in report.items() if key != "completionPublication"}, marker)
                with self.assertRaisesRegex(archive.backup.BackupError, "exists"):
                    self.create()
                self.assertEqual(older.read_bytes(), original_zip)
                self.assertEqual(archive.sidecar_path(older).read_bytes(), original_sidecar)
                self.assertEqual(archive.verify_archive(older)["manifestSha256"], old_report["manifestSha256"])

    def test_cli_post_write_sidecar_errors_succeed_only_with_exact_readback(self):
        reviewed = self.base / "reviewed-plan.json"
        reviewed.write_bytes(archive.backup.encoded(self.plan()))
        for mode in ["fsync", "close"]:
            with self.subTest(mode=mode):
                self.destination = self.base / ("cli-" + mode) / "lumen-backup-fixture.zip"
                self.destination.parent.mkdir()
                output, errors = io.StringIO(), io.StringIO()
                with self.sidecar_fault(mode), redirect_stdout(output), redirect_stderr(errors):
                    code = archive.main(["--root", str(self.root), "--archive", str(reviewed), "--destination", str(self.destination),
                        "--maximum-archive-bytes", "1000000", "--minimum-free-bytes", "0"])
                report = json.loads(output.getvalue().splitlines()[-1])
                self.assertEqual(code, 0); self.assertEqual(errors.getvalue(), "")
                self.assertEqual(report["copyStatus"], "complete")
                self.assertEqual(report["completionPublication"]["status"], "exact-readback-after-write-error")
                self.assertEqual(archive.verify_archive(self.destination)["manifestSha256"], report["manifestSha256"])

    def test_normal_publication_syncs_the_shared_owned_directory_after_both_files(self):
        original_fsync = archive.os.fsync
        calls = []
        def track_fsync(descriptor):
            info = os.fstat(descriptor)
            calls.append((stat.S_ISDIR(info.st_mode), info.st_dev, info.st_ino))
            return original_fsync(descriptor)
        with patch.object(archive.os, "fsync", side_effect=track_fsync):
            report = self.create()
        parent = self.destination.parent.stat()
        self.assertEqual(calls[-1], (True, parent.st_dev, parent.st_ino))
        self.assertEqual(sum(call[0] for call in calls), 1)
        self.assertEqual([call[2] for call in calls[:-1]],
                         [self.destination.stat().st_ino, archive.sidecar_path(self.destination).stat().st_ino])
        self.assertNotIn("completionPublication", report)

    def test_directory_sync_failure_returns_exact_readback_with_unverified_durability(self):
        older_report = self.create()
        older = self.destination
        previous = older.read_bytes(), archive.sidecar_path(older).read_bytes()
        self.destination = self.base / "directory-sync-error" / older.name
        self.destination.parent.mkdir()
        original_fsync, original_fdopen = archive.os.fsync, archive.os.fdopen
        failed, readbacks = [], []
        def fail_directory_sync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                failed.append(True)
                raise OSError("fixture owned-directory sync failure")
            return original_fsync(descriptor)
        def track_readback(descriptor, mode, *args, **kwargs):
            marker = archive.sidecar_path(self.destination)
            if mode == "rb" and marker.exists() and os.fstat(descriptor).st_ino == marker.stat().st_ino:
                readbacks.append(True)
            return original_fdopen(descriptor, mode, *args, **kwargs)
        with patch.object(archive.os, "fsync", side_effect=fail_directory_sync), \
             patch.object(archive.os, "fdopen", side_effect=track_readback):
            report = self.create()
        self.assertEqual(len(failed), 1)
        self.assertEqual(len(readbacks), 1)
        self.assertFalse(report["completionPublication"]["durabilityVerified"])
        self.assertEqual(report["completionPublication"]["status"], "exact-readback-after-write-error")
        self.assertEqual(archive.verify_archive(self.destination)["manifestSha256"], report["manifestSha256"])
        self.assertEqual((older.read_bytes(), archive.sidecar_path(older).read_bytes()), previous)
        self.assertEqual(archive.verify_archive(older)["manifestSha256"], older_report["manifestSha256"])

    def test_marker_changes_during_full_verification_cannot_return_stale_completion(self):
        original_hash, original_entry = archive.hash_stream, archive.read_entry
        for phase in ["archive-hash", "entry-read"]:
            for change in ["remove", "replace-exact", "in-place"]:
                with self.subTest(phase=phase, change=change):
                    self.destination = self.base / (phase + "-" + change) / "lumen-backup-fixture.zip"
                    self.destination.parent.mkdir()
                    self.create()
                    marker = archive.sidecar_path(self.destination)
                    marker_bytes, marker_info = marker.read_bytes(), marker.stat()
                    changed = []
                    def mutate_marker():
                        if changed:
                            return
                        changed.append(True)
                        if change == "remove":
                            marker.unlink()
                        elif change == "replace-exact":
                            replacement = marker.with_name(marker.name + ".replacement")
                            replacement.write_bytes(marker_bytes)
                            os.utime(replacement, ns=(marker_info.st_atime_ns, marker_info.st_mtime_ns))
                            os.replace(replacement, marker)
                        else:
                            value = json.loads(marker_bytes)
                            value["copyStatus"] = "invalid!"
                            altered = archive.backup.encoded(value)
                            self.assertEqual(len(altered), len(marker_bytes))
                            marker.write_bytes(altered)
                            os.utime(marker, ns=(marker_info.st_atime_ns, marker_info.st_mtime_ns))
                    def hash_and_mutate(stream):
                        result = original_hash(stream)
                        if phase == "archive-hash":
                            mutate_marker()
                        return result
                    def entry_and_mutate(handle, row, outgoing=None):
                        result = original_entry(handle, row, outgoing)
                        if phase == "entry-read":
                            mutate_marker()
                        return result
                    with patch.object(archive, "hash_stream", side_effect=hash_and_mutate), \
                         patch.object(archive, "read_entry", side_effect=entry_and_mutate):
                        with self.assertRaises(archive.backup.BackupError):
                            archive.verify_archive(self.destination)
                    self.assertEqual(len(changed), 1)
                    for name in self.paths:
                        self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_marker_removal_during_restore_precheck_or_copy_cannot_report_success(self):
        original_entry = archive.read_entry
        for phase in ["precheck", "copy"]:
            with self.subTest(phase=phase):
                self.destination = self.base / ("restore-marker-" + phase) / "lumen-backup-fixture.zip"
                self.destination.parent.mkdir()
                self.create()
                restored = self.base / ("restored-marker-" + phase)
                removed = []
                def entry_and_remove(handle, row, outgoing=None):
                    result = original_entry(handle, row, outgoing)
                    if not removed and (outgoing is not None) == (phase == "copy"):
                        archive.sidecar_path(self.destination).unlink()
                        removed.append(True)
                    return result
                with patch.object(archive, "read_entry", side_effect=entry_and_remove):
                    with self.assertRaises(archive.backup.BackupError):
                        archive.restore_sample(self.root, self.destination, restored, [self.paths[0]],
                                               minimum_free_bytes=0)
                self.assertEqual(len(removed), 1)
                self.assertTrue(self.destination.exists())
                if restored.exists():
                    self.assertEqual((restored / self.paths[0]).read_bytes(), self.original[self.paths[0]])

    def test_logical_zip_replacement_during_verification_rejects_even_with_identical_bytes(self):
        original_entry = archive.read_entry
        for change in ["replace-exact", "parent-swap"]:
            with self.subTest(change=change):
                self.destination = self.base / ("logical-verify-" + change) / "lumen-backup-fixture.zip"
                self.destination.parent.mkdir()
                self.create()
                source_bytes = self.destination.read_bytes()
                changed = []
                def entry_and_replace(handle, row, outgoing=None):
                    result = original_entry(handle, row, outgoing)
                    if not changed:
                        changed.append(True)
                        if change == "replace-exact":
                            saved = self.destination.stat()
                            replacement = self.destination.with_name("replacement.zip")
                            replacement.write_bytes(source_bytes)
                            os.utime(replacement, ns=(saved.st_atime_ns, saved.st_mtime_ns))
                            os.replace(replacement, self.destination)
                        else:
                            previous = self.destination.parent.with_name(self.destination.parent.name + "-displaced")
                            self.destination.parent.rename(previous)
                            self.destination.parent.mkdir()
                    return result
                with patch.object(archive, "read_entry", side_effect=entry_and_replace):
                    with self.assertRaises(archive.backup.BackupError):
                        archive.verify_archive(self.destination)
                self.assertEqual(len(changed), 1)

    def test_restore_directory_replacement_during_final_hash_cannot_report_verified(self):
        self.create()
        restored = self.base / "restored-final-hash"
        moved = self.base / "restored-displaced"
        original_fdopen = archive.os.fdopen
        renamed = []
        class MovingReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, *args):
                data = self.stream.read(*args)
                if data and not renamed:
                    restored.rename(moved)
                    renamed.append(True)
                return data
        def move_during_restored_hash(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            output = restored / self.paths[0]
            if mode == "rb" and output.exists() and os.fstat(descriptor).st_ino == output.stat().st_ino:
                return MovingReader(stream)
            return stream
        with patch.object(archive.os, "fdopen", side_effect=move_during_restored_hash):
            with self.assertRaises(archive.backup.BackupError):
                archive.restore_sample(self.root, self.destination, restored, [self.paths[0]], minimum_free_bytes=0)
        self.assertEqual(len(renamed), 1)
        self.assertFalse(restored.exists())
        self.assertEqual((moved / self.paths[0]).read_bytes(), self.original[self.paths[0]])
        self.assertEqual(archive.verify_archive(self.destination)["verifiedFiles"], len(self.paths))

    def test_output_mutation_during_archive_checksum_cannot_publish_completion(self):
        original_hash = archive.hash_stream
        mutated = []
        class MutatingArchiveReader:
            def __init__(self, stream):
                self.stream = stream
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, *args):
                block = self.stream.read(*args)
                if block and not mutated:
                    previous = self_destination.stat()
                    with self_destination.open("r+b") as changed:
                        changed.seek(60)
                        byte = changed.read(1)
                        changed.seek(60)
                        changed.write(bytes([byte[0] ^ 1]))
                    os.utime(self_destination, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                    mutated.append(True)
                return block
        self_destination = self.destination
        with patch.object(archive, "hash_stream", side_effect=lambda stream: original_hash(MutatingArchiveReader(stream))):
            with self.assertRaises(archive.backup.BackupError):
                self.create()
        self.assertEqual(len(mutated), 1)
        self.assertTrue(self.destination.exists())
        self.assertFalse(archive.sidecar_path(self.destination).exists())
        for name in self.paths:
            self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_normal_marker_mutation_during_sync_cannot_publish_wrong_completion(self):
        original_fsync = archive.os.fsync
        marker = archive.sidecar_path(self.destination)
        mutated = []
        def sync_and_mutate(descriptor):
            result = original_fsync(descriptor)
            if not mutated and marker.exists() and os.fstat(descriptor).st_ino == marker.stat().st_ino:
                before = marker.stat()
                value = json.loads(marker.read_bytes())
                value["planSha256"] = ("a" if value["planSha256"][0] != "a" else "b") + value["planSha256"][1:]
                marker.write_bytes(archive.backup.encoded(value))
                os.utime(marker, ns=(before.st_atime_ns, before.st_mtime_ns))
                mutated.append(True)
            return result
        with patch.object(archive.os, "fsync", side_effect=sync_and_mutate):
            with self.assertRaises(archive.backup.BackupError):
                self.create()
        self.assertEqual(len(mutated), 1)
        self.assertTrue(self.destination.exists()); self.assertTrue(marker.exists())
        with self.assertRaises(archive.backup.BackupError):
            archive.verify_archive(self.destination)

    def test_archive_mutation_during_successful_sync_cannot_publish_completion(self):
        original_fsync = archive.os.fsync
        mutated = []
        def sync_and_mutate(descriptor):
            result = original_fsync(descriptor)
            if not mutated and self.destination.exists() and os.fstat(descriptor).st_ino == self.destination.stat().st_ino:
                before = self.destination.stat()
                with self.destination.open("r+b") as changed:
                    changed.seek(60)
                    byte = changed.read(1)
                    changed.seek(60)
                    changed.write(bytes([byte[0] ^ 1]))
                os.utime(self.destination, ns=(before.st_atime_ns, before.st_mtime_ns))
                mutated.append(True)
            return result
        with patch.object(archive.os, "fsync", side_effect=sync_and_mutate):
            with self.assertRaises(archive.backup.BackupError):
                self.create()
        self.assertEqual(len(mutated), 1)
        self.assertTrue(self.destination.exists())
        self.assertFalse(archive.sidecar_path(self.destination).exists())

    def test_transient_post_publication_stat_failure_uses_one_bounded_error_readback(self):
        for phase in ["writer-final-marker", "snapshot-final-archive", "snapshot-final-marker"]:
            with self.subTest(phase=phase):
                self.destination = self.base / phase / "lumen-backup-fixture.zip"
                self.destination.parent.mkdir()
                checksum = archive.sidecar_path(self.destination)
                original_stat, original_fdopen = archive.os.stat, archive.os.fdopen
                marker_checks, faults, readbacks = [], [], []
                def one_transient_stat(name, *args, **kwargs):
                    if kwargs.get("dir_fd") is not None and name == checksum.name:
                        marker_checks.append(True)
                        selected = (phase == "writer-final-marker" and len(marker_checks) == 1) or \
                                   (phase == "snapshot-final-marker" and len(marker_checks) == 3)
                    else:
                        selected = kwargs.get("dir_fd") is not None and name == self.destination.name and \
                                   phase == "snapshot-final-archive" and checksum.exists()
                    if selected and not faults:
                        faults.append(True)
                        raise OSError("fixture one transient post-publication stat failure")
                    return original_stat(name, *args, **kwargs)
                def count_marker_readback(descriptor, mode, *args, **kwargs):
                    if mode == "rb" and checksum.exists() and os.fstat(descriptor).st_ino == checksum.stat().st_ino:
                        readbacks.append(True)
                    return original_fdopen(descriptor, mode, *args, **kwargs)
                error = None
                with patch.object(archive.os, "stat", side_effect=one_transient_stat), \
                     patch.object(archive.os, "fdopen", side_effect=count_marker_readback):
                    try:
                        report = self.create()
                    except archive.backup.BackupError as failure:
                        error = failure
                proof = archive.verify_archive(self.destination)
                self.assertEqual(len(faults), 1)
                self.assertIsNone(error, "the complete owned marker already passes current-byte verification")
                self.assertEqual(len(readbacks), 2, "one normal payload check plus one bounded error reconciliation")
                self.assertEqual(report["copyStatus"], "complete")
                self.assertEqual(report["manifestSha256"], proof["manifestSha256"])
                self.assertEqual(report["completionPublication"]["status"], "exact-readback-after-write-error")

    def test_post_publication_reconciliation_stays_bounded_and_rejects_changed_identity(self):
        for failure_type in [OSError, archive.backup.BackupError]:
            with self.subTest(failure_type=failure_type):
                self.destination = self.base / failure_type.__name__ / "lumen-backup-fixture.zip"
                self.destination.parent.mkdir()
                checksum = archive.sidecar_path(self.destination)
                original = archive.require_owned_path
                failures = []
                def reject_final_archive(path, parent_identity, file_identity, label):
                    if Path(path) == self.destination and checksum.exists():
                        failures.append(True)
                        raise failure_type("fixture persistent I/O or changed output identity")
                    return original(path, parent_identity, file_identity, label)
                with patch.object(archive, "require_owned_path", side_effect=reject_final_archive):
                    with self.assertRaisesRegex(archive.backup.BackupError, "persistent I/O or changed output identity"):
                        self.create()
                self.assertEqual(len(failures), 2 if failure_type is OSError else 1)
                self.assertTrue(self.destination.exists()); self.assertTrue(checksum.exists())
                for name in self.paths:
                    self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_partial_malformed_or_pre_marker_failures_remain_unverifiable_and_preserve_older_backup(self):
        self.create()
        older = self.destination
        original_zip, original_sidecar = older.read_bytes(), archive.sidecar_path(older).read_bytes()
        for mode in ["partial", "malformed", "different-json", "short", "before-write", "before-create"]:
            with self.subTest(mode=mode):
                self.destination = self.base / mode / older.name
                self.destination.parent.mkdir()
                with self.sidecar_fault(mode), self.assertRaises(archive.backup.BackupError):
                    self.create()
                self.assertTrue(self.destination.exists(), "retain the completed archive after its marker fails")
                with self.assertRaises(archive.backup.BackupError):
                    archive.verify_archive(self.destination)
                checksum = archive.sidecar_path(self.destination)
                if mode == "before-create":
                    self.assertFalse(checksum.exists())
                else:
                    self.assertTrue(checksum.exists(), "retain incomplete marker bytes rather than overwrite or delete them")
                self.assertEqual(older.read_bytes(), original_zip)
                self.assertEqual(archive.sidecar_path(older).read_bytes(), original_sidecar)
                self.assertEqual(archive.verify_archive(older)["verifiedFiles"], len(self.paths))
        for name in self.paths:
            self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_exact_existing_marker_cannot_recover_a_failed_exclusive_create(self):
        marker = archive.sidecar_path(self.destination)
        value = {"copyStatus": "complete", "test": "preexisting exact bytes"}
        expected = archive.backup.encoded(value)
        marker.write_bytes(expected)
        with self.assertRaises(FileExistsError):
            archive.write_exclusive_json(marker, value)
        self.assertEqual(marker.read_bytes(), expected)

    def test_cli_and_verifier_both_reject_incomplete_completion_marker(self):
        reviewed = self.base / "reviewed-plan.json"
        reviewed.write_bytes(archive.backup.encoded(self.plan()))
        for mode in ["partial", "malformed", "different-json", "short", "before-write", "before-create"]:
            with self.subTest(mode=mode):
                self.destination = self.base / ("failed-cli-" + mode) / "lumen-backup-fixture.zip"
                self.destination.parent.mkdir()
                output, errors = io.StringIO(), io.StringIO()
                with self.sidecar_fault(mode), redirect_stdout(output), redirect_stderr(errors):
                    with self.assertRaises(SystemExit) as failure:
                        archive.main(["--root", str(self.root), "--archive", str(reviewed), "--destination", str(self.destination),
                            "--maximum-archive-bytes", "1000000", "--minimum-free-bytes", "0"])
                self.assertEqual(failure.exception.code, 2)
                self.assertTrue(all(json.loads(line).get("copyStatus") != "complete" for line in output.getvalue().splitlines()))
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as verification:
                        archive.main(["--verify", str(self.destination)])
                self.assertEqual(verification.exception.code, 2)

    def test_completion_marker_mutation_during_readback_rejects_and_retains_partial_backup(self):
        checksum = archive.sidecar_path(self.destination)
        original_fdopen = archive.os.fdopen
        changed = []
        class MutatingReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, size):
                data = self.stream.read(size)
                before = checksum.stat()
                checksum.write_bytes(b"x" * len(data))
                os.utime(checksum, ns=(before.st_atime_ns, before.st_mtime_ns))
                changed.append(True)
                return data
        def mutate_marker(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            if mode == "rb" and checksum.exists() and os.fstat(descriptor).st_ino == checksum.stat().st_ino:
                return MutatingReader(stream)
            return stream
        with self.sidecar_fault("fsync"), patch.object(archive.os, "fdopen", side_effect=mutate_marker):
            with self.assertRaisesRegex(archive.backup.BackupError, "changed during error readback"):
                self.create()
        self.assertTrue(changed)
        self.assertTrue(self.destination.exists()); self.assertTrue(checksum.exists())
        with self.assertRaises(archive.backup.BackupError):
            archive.verify_archive(self.destination)

    def test_logical_parent_replacement_during_marker_readback_cannot_report_completion(self):
        checksum = archive.sidecar_path(self.destination)
        displaced = self.destination.parent.with_name("Lumen-displaced")
        original_fdopen = archive.os.fdopen
        moved = []
        class MovingReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, size):
                data = self.stream.read(size)
                moved.append({"archive": self_destination.read_bytes(), "marker": checksum.read_bytes()})
                checksum.parent.rename(displaced)
                checksum.parent.mkdir()
                return data
        self_destination = self.destination
        def move_parent(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            if mode == "rb" and checksum.exists() and os.fstat(descriptor).st_ino == checksum.stat().st_ino:
                return MovingReader(stream)
            return stream
        with self.sidecar_fault("fsync"), patch.object(archive.os, "fdopen", side_effect=move_parent):
            with self.assertRaisesRegex(archive.backup.BackupError, "logical parent changed"):
                self.create()
        self.assertEqual(len(moved), 1)
        self.assertFalse(self.destination.exists()); self.assertFalse(checksum.exists())
        with self.assertRaises(archive.backup.BackupError):
            archive.verify_archive(self.destination)
        preserved = displaced / self.destination.name
        self.assertEqual(preserved.read_bytes(), moved[0]["archive"])
        self.assertEqual(archive.sidecar_path(preserved).read_bytes(), moved[0]["marker"])
        self.assertEqual(archive.verify_archive(preserved)["verifiedFiles"], len(self.paths))
        for name in self.paths:
            self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_normal_publication_parent_swaps_cannot_report_a_missing_logical_archive(self):
        self.create()
        older = self.destination
        older_zip, older_marker = older.read_bytes(), archive.sidecar_path(older).read_bytes()
        for phase in ["before-sidecar", "during-marker"]:
            with self.subTest(phase=phase):
                self.destination = self.base / phase / "Lumen" / older.name
                self.destination.parent.mkdir(parents=True)
                checksum = archive.sidecar_path(self.destination)
                displaced = self.destination.parent.with_name("Lumen-displaced")
                original_writer, original_fdopen = archive.write_exclusive_json, archive.os.fdopen
                moved = []
                def move_parent():
                    moved.append({"archive": self_destination.read_bytes(),
                                  "marker": checksum.read_bytes() if checksum.exists() else None})
                    checksum.parent.rename(displaced)
                    checksum.parent.mkdir()
                def moving_publication(path, value):
                    if phase == "before-sidecar":
                        move_parent()
                    return original_writer(path, value)
                class MovingWriter:
                    def __init__(self, stream):
                        self.stream = stream
                    def __enter__(self):
                        self.stream.__enter__()
                        return self
                    def __exit__(self, *args):
                        return self.stream.__exit__(*args)
                    def __getattr__(self, name):
                        return getattr(self.stream, name)
                    def write(self, data):
                        count = self.stream.write(data)
                        self.stream.flush()
                        move_parent()
                        return count
                def moving_fdopen(descriptor, mode, *args, **kwargs):
                    stream = original_fdopen(descriptor, mode, *args, **kwargs)
                    return MovingWriter(stream) if mode == "wb" and phase == "during-marker" else stream
                self_destination = self.destination
                with patch.object(archive, "write_exclusive_json", side_effect=moving_publication), \
                     patch.object(archive.os, "fdopen", side_effect=moving_fdopen):
                    with self.assertRaises(archive.backup.BackupError):
                        self.create()
                self.assertEqual(len(moved), 1)
                self.assertFalse(self.destination.exists())
                with self.assertRaises(archive.backup.BackupError):
                    archive.verify_archive(self.destination)
                preserved = displaced / self.destination.name
                self.assertEqual(preserved.read_bytes(), moved[0]["archive"])
                if phase == "during-marker":
                    self.assertEqual(archive.sidecar_path(preserved).read_bytes(), moved[0]["marker"])
                    self.assertEqual(archive.verify_archive(preserved)["verifiedFiles"], len(self.paths))
                self.assertEqual(older.read_bytes(), older_zip)
                self.assertEqual(archive.sidecar_path(older).read_bytes(), older_marker)
        for name in self.paths:
            self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_archive_full_identity_is_checked_after_normal_marker_publication(self):
        original_writer = archive.write_exclusive_json
        mutated = []
        def mutate_archive_before_marker(path, value):
            before = self.destination.stat()
            with self.destination.open("r+b") as stream:
                first = stream.read(1)
                stream.seek(0); stream.write(bytes([first[0] ^ 1]))
            os.utime(self.destination, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(self.destination.stat().st_size, before.st_size)
            self.assertEqual(self.destination.stat().st_mtime_ns, before.st_mtime_ns)
            mutated.append(True)
            return original_writer(path, value)
        with patch.object(archive, "write_exclusive_json", side_effect=mutate_archive_before_marker):
            with self.assertRaisesRegex(archive.backup.BackupError, "Backup archive logical file changed"):
                self.create()
        self.assertEqual(len(mutated), 1)
        self.assertTrue(self.destination.exists()); self.assertTrue(archive.sidecar_path(self.destination).exists())
        with self.assertRaises(archive.backup.BackupError):
            archive.verify_archive(self.destination)
        for name in self.paths:
            self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_zip_preserves_paths_bytes_and_hash_bound_manifest(self):
        report = self.create()
        with zipfile.ZipFile(self.destination) as handle:
            self.assertEqual(handle.namelist(), sorted(self.paths) + [archive.MANIFEST_NAME])
            manifest = json.loads(handle.read(archive.MANIFEST_NAME))
            for row in manifest["files"]:
                self.assertEqual(handle.read(row["path"]), self.original[row["path"]])
                self.assertEqual(row["sha256"], hashlib.sha256(self.original[row["path"]]).hexdigest())
                self.assertEqual(handle.getinfo(row["path"]).compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual(handle.getinfo(row["path"]).date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(manifest["remoteSyncStatus"], "pending")
            self.assertEqual(manifest["manifestSha256"], report["manifestSha256"])
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(archive.sidecar_path(self.destination).stat().st_mode & 0o777, 0o600)
        self.assertEqual(report["archiveSha256"], hashlib.sha256(self.destination.read_bytes()).hexdigest())
        self.assertEqual(report["archiveMd5"], hashlib.md5(self.destination.read_bytes()).hexdigest())
        self.assertLess(report["archiveBytes"], report["sourceBytes"])
        self.assertEqual(set(self.destination.parent.iterdir()), {self.destination, archive.sidecar_path(self.destination)})
        for name in self.paths:
            self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_verify_all_and_restore_explicit_sample(self):
        report = self.create()
        proof = archive.verify_archive(self.destination)
        self.assertEqual(proof["verifiedFiles"], len(self.paths))
        self.assertEqual(proof["verificationScope"], "local-archive-all-files")
        self.assertFalse(proof["remoteSyncVerified"])
        restored = self.base / "restored"
        sample = archive.restore_sample(self.root, self.destination, restored, [self.paths[0], self.paths[1]])
        self.assertEqual(sample["verifiedFiles"], 2)
        self.assertEqual(sample["manifestSha256"], report["manifestSha256"])
        for name in [self.paths[0], self.paths[1]]:
            self.assertEqual((restored / name).read_bytes(), self.original[name])
            self.assertEqual((restored / name).stat().st_mode & 0o777, 0o600)
        self.assertFalse((restored / self.paths[3]).exists())

    def test_existing_archive_or_checksum_file_is_preserved(self):
        for path in [self.destination, archive.sidecar_path(self.destination)]:
            with self.subTest(path=path):
                path.write_bytes(b"older backup")
                with self.assertRaisesRegex(archive.backup.BackupError, "exists"):
                    self.create()
                self.assertEqual(path.read_bytes(), b"older backup")
                path.unlink()

    def test_wrong_filename_public_destination_and_symlink_parent_reject(self):
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        other = self.base / "real-drive"
        other.mkdir()
        link = self.base / "linked-drive"
        link.symlink_to(other, target_is_directory=True)
        for destination in [self.destination.with_name("arbitrary.zip"), public / self.destination.name,
                            link / self.destination.name]:
            with self.subTest(path=destination):
                with self.assertRaises(archive.backup.BackupError):
                    archive.archive_snapshot(self.root, destination, self.plan(), maximum_archive_bytes=1_000_000,
                                             minimum_free_bytes=0)
                self.assertFalse(destination.exists())

    def test_changed_source_clock_and_reserved_manifest_name_reject_before_write(self):
        plan = self.plan()
        (self.root / self.paths[0]).write_bytes(b"changed")
        with self.assertRaisesRegex(archive.backup.BackupError, "changed"):
            self.create(plan)
        self.assertFalse(self.destination.exists())
        (self.root / archive.MANIFEST_NAME).write_bytes(b"collision")
        plan = archive.backup.prepare_fileset(self.root, [archive.MANIFEST_NAME], "lumen-backup-fixture")
        with self.assertRaisesRegex(archive.backup.BackupError, "reserved"):
            self.create(plan)
        self.assertFalse(self.destination.exists())

    def test_same_size_replacement_preserving_mtime_after_plan_rejects_before_archive(self):
        plan = self.plan()
        source = self.root / self.paths[0]
        before = source.stat()
        replacement = source.with_name("replacement.md")
        replacement.write_bytes(b"x" * before.st_size)
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(source)
        self.assertEqual(source.stat().st_size, before.st_size)
        self.assertEqual(source.stat().st_mtime_ns, before.st_mtime_ns)
        with self.assertRaisesRegex(archive.backup.BackupError, "identity|changed"):
            self.create(plan)
        self.assertFalse(self.destination.exists())

    def test_same_size_in_place_change_preserving_mtime_after_plan_rejects_before_archive(self):
        plan = self.plan()
        source = self.root / self.paths[0]
        before = source.stat()
        with source.open("r+b") as stream:
            stream.write(b"x" * before.st_size)
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(source.stat().st_ino, before.st_ino)
        self.assertEqual(source.stat().st_size, before.st_size)
        self.assertEqual(source.stat().st_mtime_ns, before.st_mtime_ns)
        with self.assertRaisesRegex(archive.backup.BackupError, "identity|changed"):
            self.create(plan)
        self.assertFalse(self.destination.exists())

    def test_legacy_plan_cannot_create_new_archive_but_existing_archive_still_verifies(self):
        plan = self.plan()
        historic = copy.deepcopy(plan)
        historic["schemaVersion"] = 1
        for row in historic["files"]:
            row.pop("sourceIdentity", None)
        historic = archive.backup.signed(historic, "planSha256")
        with self.assertRaisesRegex(archive.backup.BackupError, "identity|fresh plan"):
            self.create(historic)
        self.assertFalse(self.destination.exists())
        self.create(plan)
        with zipfile.ZipFile(self.destination) as handle:
            self.assertEqual(json.loads(handle.read(archive.MANIFEST_NAME))["schemaVersion"], 1)
        self.assertEqual(archive.verify_archive(self.destination)["verifiedFiles"], len(self.paths))

    def test_in_place_mutation_during_read_cannot_reach_compressed_entry(self):
        plan = self.plan()
        name = sorted(self.paths)[0]
        source = self.root / name
        before = source.stat()
        original_fdopen = archive.os.fdopen
        changed = []
        class MutatingReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def read(self, size):
                block = self.stream.read(size)
                if not changed:
                    with source.open("r+b") as output:
                        output.write(b"x" * before.st_size)
                    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
                    changed.append(True)
                return block
        def mutate_after_read(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            return MutatingReader(stream) if mode == "rb" and os.fstat(descriptor).st_ino == before.st_ino else stream
        with patch.object(archive.os, "fdopen", side_effect=mutate_after_read):
            with self.assertRaisesRegex(archive.backup.BackupError, "identity changed"):
                self.create(plan)
        self.assertTrue(changed)
        self.assertEqual(source.stat().st_mtime_ns, before.st_mtime_ns)
        with zipfile.ZipFile(self.destination) as handle:
            self.assertEqual(handle.read(name), b"")
        self.assertFalse(archive.sidecar_path(self.destination).exists())

    def test_expected_hash_failure_has_no_completion_sidecar(self):
        plan = self.plan(expected_hashes={self.paths[1]: "a" * 64})
        with self.assertRaisesRegex(archive.backup.BackupError, "hash"):
            self.create(plan)
        self.assertTrue(self.destination.exists())
        self.assertFalse(archive.sidecar_path(self.destination).exists())
        with self.assertRaises(archive.backup.BackupError):
            archive.verify_archive(self.destination)

    def test_insufficient_space_for_explicit_cap_keeps_destination_untouched(self):
        with patch.object(archive.shutil, "disk_usage", return_value=type("Usage", (), {"free": 100})()):
            with self.assertRaisesRegex(archive.backup.BackupError, "space"):
                self.create()
        self.assertFalse(self.destination.exists())

    def test_stream_output_cap_is_enforced_without_completed_sidecar(self):
        with self.assertRaisesRegex(archive.backup.BackupError, "cap"):
            archive.archive_snapshot(self.root, self.destination, self.plan(), maximum_archive_bytes=100,
                                     minimum_free_bytes=0)
        self.assertLessEqual(self.destination.stat().st_size, 100)
        self.assertFalse(archive.sidecar_path(self.destination).exists())

    def test_free_space_reserve_is_checked_during_each_chunk(self):
        calls = []
        def free_space(_):
            calls.append(1)
            return type("Usage", (), {"free": 2_000_000 if len(calls) < 5 else 0})()
        with patch.object(archive.shutil, "disk_usage", side_effect=free_space):
            with self.assertRaisesRegex(archive.backup.BackupError, "space"):
                self.create()
        self.assertGreaterEqual(len(calls), 5)
        self.assertFalse(archive.sidecar_path(self.destination).exists())

    def test_source_change_after_earlier_entry_prevents_completed_sidecar(self):
        plan = self.plan()
        first = sorted(self.paths)[0]
        def change_source(done, *_):
            if done == 2:
                (self.root / first).write_bytes(b"x" * len(self.original[first]))
        with self.assertRaisesRegex(archive.backup.BackupError, "changed"):
            self.create(plan, progress=change_source)
        self.assertFalse(archive.sidecar_path(self.destination).exists())

    def test_same_clock_replacement_after_earlier_entry_rejects(self):
        first = sorted(self.paths)[0]
        def replace_source(done, *_):
            if done == 2:
                path = self.root / first
                previous = path.stat()
                replacement = path.with_name("replacement.mp3")
                replacement.write_bytes(b"x" * previous.st_size)
                os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                replacement.replace(path)
        with self.assertRaisesRegex(archive.backup.BackupError, "identity changed"):
            self.create(progress=replace_source)
        self.assertFalse(archive.sidecar_path(self.destination).exists())

    def test_source_ancestor_symlink_at_open_cannot_copy_unselected_bytes(self):
        original = archive.open_source
        switched = []
        def redirected(root, row):
            if not switched:
                source = root / row["path"]
                outside = self.base / "unselected"
                outside.mkdir()
                (outside / source.name).write_bytes(b"x" * row["bytes"])
                source.parent.rename(source.parent.with_name(source.parent.name + "-saved"))
                source.parent.symlink_to(outside, target_is_directory=True)
                switched.append(True)
            return original(root, row)
        with patch.object(archive, "open_source", side_effect=redirected):
            with self.assertRaises(archive.backup.BackupError):
                self.create()
        self.assertFalse(archive.sidecar_path(self.destination).exists())

    def test_archive_tamper_unknown_sample_and_existing_restore_reject_before_restore(self):
        self.create()
        with self.assertRaisesRegex(archive.backup.BackupError, "Unknown"):
            archive.restore_sample(self.root, self.destination, self.base / "unknown", ["unknown.md"])
        restored = self.base / "restored"
        restored.mkdir()
        with self.assertRaisesRegex(archive.backup.BackupError, "exists"):
            archive.restore_sample(self.root, self.destination, restored, [self.paths[0]])
        content = bytearray(self.destination.read_bytes())
        content[60] ^= 1
        self.destination.write_bytes(content)
        with self.assertRaisesRegex(archive.backup.BackupError, "hash"):
            archive.restore_sample(self.root, self.destination, self.base / "tampered", [self.paths[0]])
        self.assertFalse((self.base / "tampered").exists())

    def test_restore_public_path_and_symlink_parent_reject(self):
        self.create()
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        real = self.base / "real-restore"
        real.mkdir()
        link = self.base / "linked-restore"
        link.symlink_to(real, target_is_directory=True)
        for destination in [public / "restored", link / "restored"]:
            with self.assertRaises(archive.backup.BackupError):
                archive.restore_sample(self.root, self.destination, destination, [self.paths[0]])
            self.assertFalse(destination.exists())

    def test_bounded_compression_estimate_does_not_change_or_copy_sources(self):
        plan = self.plan()
        estimate = archive.estimate_archive(self.root, plan, [self.paths[1], self.paths[2]], window_bytes=1024)
        self.assertLessEqual(estimate["sampledBytes"], 2 * 3 * 1024)
        self.assertGreater(estimate["estimatedArchiveBytes"], 0)
        self.assertFalse(estimate["guaranteedToFit"])
        self.assertGreaterEqual(estimate["groups"]["other"]["estimatedBytes"],
                                sum(len(self.original[name]) for name in [self.paths[0], self.paths[3]]))
        self.assertFalse(self.destination.exists())
        for name in self.paths:
            self.assertEqual((self.root / name).read_bytes(), self.original[name])

    def test_estimate_rejects_unselected_or_secret_samples_and_invalid_plan(self):
        for names in [["unselected.wav"], [".env"], [self.paths[1], self.paths[1]]]:
            with self.assertRaises(archive.backup.BackupError):
                archive.estimate_archive(self.root, self.plan(), names, window_bytes=1024)
        plan = copy.deepcopy(self.plan())
        plan["totalBytes"] = 1
        with self.assertRaisesRegex(archive.backup.BackupError, "digest"):
            self.create(plan)

    def test_malformed_manifest_duplicate_and_symlink_zip_entries_reject(self):
        self.create()
        with zipfile.ZipFile(self.destination) as handle:
            entries = [(info.filename, handle.read(info)) for info in handle.infolist()]
        for mutation in ["duplicate", "symlink", "manifest"]:
            with self.subTest(mutation=mutation):
                changed = self.base / f"{mutation}.zip"
                with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as handle:
                    for name, content in entries:
                        if name == archive.MANIFEST_NAME and mutation == "manifest":
                            manifest = json.loads(content)
                            manifest["files"][0]["path"] = "../outside"
                            content = archive.backup.encoded(archive.backup.signed(manifest, "manifestSha256"))
                        info = zipfile.ZipInfo(name)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        if mutation == "symlink" and name != archive.MANIFEST_NAME:
                            info.external_attr = (stat.S_IFLNK | 0o600) << 16
                        handle.writestr(info, content)
                    if mutation == "duplicate":
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", UserWarning)
                            handle.writestr(entries[0][0], entries[0][1])
                original_sidecar = json.loads(archive.sidecar_path(self.destination).read_text())
                data = changed.read_bytes()
                original_sidecar.update(archiveBytes=len(data), archiveSha256=hashlib.sha256(data).hexdigest(),
                                        archiveMd5=hashlib.md5(data).hexdigest())
                archive.sidecar_path(changed).write_text(json.dumps(original_sidecar))
                with self.assertRaises(archive.backup.BackupError):
                    archive.verify_archive(changed)

    def test_archive_and_sidecar_symlinks_cannot_supply_restore_bytes(self):
        self.create()
        for path in [self.destination, archive.sidecar_path(self.destination)]:
            with self.subTest(path=path):
                moved = path.with_name(path.name + ".saved")
                path.rename(moved)
                path.symlink_to(moved)
                with self.assertRaises(archive.backup.BackupError):
                    archive.verify_archive(self.destination)
                path.unlink()
                moved.rename(path)

    def test_restore_cli_requires_explicit_root_and_samples(self):
        self.create()
        restored = self.base / "restore-cli"
        for extra in [[], ["--root", str(self.root)]]:
            with self.assertRaises(SystemExit) as failure:
                archive.main(["--restore-sample", str(self.destination), "--destination", str(restored)] + extra)
            self.assertEqual(failure.exception.code, 2)
        self.assertFalse(restored.exists())

    def test_restore_ancestor_symlink_during_copy_cannot_leak_bytes(self):
        self.create()
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        restored = self.base / "restore-race"
        original = archive.read_entry
        calls = []
        def redirect_after_precheck(handle, row, outgoing=None):
            if outgoing is not None and not calls:
                calls.append(True)
                link = restored / "Draft"
                link.symlink_to(public, target_is_directory=True)
            return original(handle, row, outgoing)
        with patch.object(archive, "read_entry", side_effect=redirect_after_precheck):
            with self.assertRaises(archive.backup.BackupError):
                archive.restore_sample(self.root, self.destination, restored, [self.paths[1], self.paths[0]],
                                       minimum_free_bytes=0)
        self.assertEqual(list(public.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
