"""Compressed backups preserve original bytes without a second full copy."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
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
