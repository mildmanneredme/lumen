"""Backup fixtures protect immutable copies and prove a small restore."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/backup_release.py"
SPEC = importlib.util.spec_from_file_location("backup_release", SCRIPT)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class BackupReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.paths = ["Draft/v6/part1.md", "Audiobook/v8/raw/chapter-001-001.wav",
                      "Audiobook/v8/mastered/chapter-001.mp3"]
        for index, name in enumerate(self.paths):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((f"fixture {index}: " * (index + 1)).encode())
        self.destination = self.base / "drive" / "Lumen" / "lumen-backup-fixture"
        self.destination.parent.mkdir(parents=True)

    def plan(self, **kwargs):
        return backup.prepare_fileset(self.root, self.paths, "lumen-backup-fixture", **kwargs)

    def run_copy(self, plan=None):
        return backup.copy_snapshot(self.root, self.destination, plan or self.plan(), minimum_free_bytes=0)

    def test_plan_is_deterministic_and_keeps_large_file_hashing_for_copy(self):
        first = self.plan()
        self.assertEqual(first, self.plan())
        self.assertEqual([row["path"] for row in first["files"]], sorted(self.paths))
        self.assertEqual(first["totalBytes"], sum((self.root / name).stat().st_size for name in self.paths))
        self.assertEqual(first["copyStatus"], "planned")

    def test_copy_hashes_bytes_preserves_sources_and_never_claims_remote_sync(self):
        before = {name: (self.root / name).read_bytes() for name in self.paths}
        manifest = self.run_copy()
        self.assertEqual(manifest["copyStatus"], "complete")
        self.assertEqual(manifest["remoteSyncStatus"], "pending")
        for row in manifest["files"]:
            self.assertEqual((self.destination / "files" / row["path"]).read_bytes(), before[row["path"]])
            self.assertEqual((self.root / row["path"]).read_bytes(), before[row["path"]])
            self.assertEqual(row["sha256"], hashlib.sha256(before[row["path"]]).hexdigest())
        self.assertEqual(json.loads((self.destination / "backup-manifest.json").read_text()), manifest)

    def test_backup_manifest_is_the_final_completion_metadata_write(self):
        original = backup.write_json
        writes = []
        def tracked_write(path, value, **kwargs):
            writes.append((path.name, copy.deepcopy(value)))
            return original(path, value, **kwargs)
        with patch.object(backup, "write_json", side_effect=tracked_write):
            manifest = self.run_copy()
        self.assertEqual(writes[-1][0], "backup-manifest.json")
        state = json.loads((self.destination / "backup-state.json").read_text())
        self.assertEqual(state["copyStatus"], "prepared")
        self.assertEqual(state["copiedBytes"], manifest["totalBytes"])
        self.assertNotIn("manifestSha256", state)
        self.assertTrue(all(value["copyStatus"] != "complete" for name, value in writes if name == "backup-state.json"))
        self.assertTrue(all("manifestSha256" not in value for name, value in writes if name == "backup-state.json"))
        self.assertNotIn("error", state)
        self.assertEqual(backup.verify_snapshot(self.destination)["manifestSha256"], manifest["manifestSha256"])

    def test_completion_metadata_failures_keep_new_copy_incomplete_and_prior_backup_immutable(self):
        earlier = self.destination.parent / "lumen-backup-earlier"
        earlier_plan = backup.prepare_fileset(self.root, self.paths, earlier.name)
        backup.copy_snapshot(self.root, earlier, earlier_plan, minimum_free_bytes=0)
        prior = {p.relative_to(earlier).as_posix(): p.read_bytes() for p in earlier.rglob("*") if p.is_file()}
        original = backup.write_json
        for failed_name in ["backup-state.json", "backup-manifest.json"]:
            with self.subTest(failed_name=failed_name):
                destination = self.destination.parent / ("lumen-failed-" + failed_name.replace(".json", ""))
                plan = backup.prepare_fileset(self.root, self.paths, destination.name)
                failed = []
                def failing_write(path, value, **kwargs):
                    if path.name == failed_name and value.get("copyStatus") in {"prepared", "complete"}:
                        failed.append(True)
                        raise OSError("injected final metadata failure")
                    return original(path, value, **kwargs)
                with patch.object(backup, "write_json", side_effect=failing_write):
                    with self.assertRaisesRegex(backup.BackupError, "incomplete.*injected final metadata failure"):
                        backup.copy_snapshot(self.root, destination, plan, minimum_free_bytes=0)
                self.assertTrue(failed)
                self.assertFalse((destination / "backup-manifest.json").exists())
                state = json.loads((destination / "backup-state.json").read_text())
                self.assertEqual(state["copyStatus"], "incomplete")
                self.assertNotIn("manifestSha256", state)
                self.assertIn("injected final metadata failure", state["error"])
                with self.assertRaises(backup.BackupError):
                    backup.verify_snapshot(destination)
                for name in self.paths:
                    self.assertEqual((destination / "files" / name).read_bytes(), (self.root / name).read_bytes())
                self.assertEqual({p.relative_to(earlier).as_posix(): p.read_bytes() for p in earlier.rglob("*") if p.is_file()}, prior)
                self.assertEqual(backup.verify_snapshot(earlier)["verifiedFiles"], len(self.paths))

    def test_error_after_manifest_publication_never_downgrades_a_completed_backup(self):
        original = backup.write_json
        published = []
        def published_then_error(path, value, **kwargs):
            result = original(path, value, **kwargs)
            if path.name == "backup-manifest.json":
                published.append(True)
                raise OSError("injected post-publication cleanup failure")
            return result
        with patch.object(backup, "write_json", side_effect=published_then_error):
            manifest = self.run_copy()
        self.assertTrue(published)
        state = json.loads((self.destination / "backup-state.json").read_text())
        self.assertEqual(state["copyStatus"], "prepared")
        self.assertNotIn("error", state)
        self.assertNotIn("manifestSha256", state)
        self.assertEqual(backup.verify_snapshot(self.destination)["manifestSha256"], manifest["manifestSha256"])

    def test_persistent_completion_io_failure_does_not_claim_completion_or_mask_primary_error(self):
        earlier = self.destination.parent / "lumen-backup-earlier"
        earlier_plan = backup.prepare_fileset(self.root, self.paths, earlier.name)
        backup.copy_snapshot(self.root, earlier, earlier_plan, minimum_free_bytes=0)
        prior = {p.relative_to(earlier).as_posix(): p.read_bytes() for p in earlier.rglob("*") if p.is_file()}
        original = backup.write_json
        for phase in ["state", "manifest"]:
            with self.subTest(phase=phase):
                destination = self.destination.parent / ("lumen-persistent-" + phase)
                plan = backup.prepare_fileset(self.root, self.paths, destination.name)
                primary = "primary " + phase + " completion write failure"
                failed = []
                diagnostic_failures = []
                def persistent_failure(path, value, **kwargs):
                    if failed and path.name == "backup-state.json":
                        diagnostic_failures.append(True)
                        raise OSError("secondary diagnostic write failure")
                    if ((phase == "state" and path.name == "backup-state.json"
                         and value.get("copyStatus") in {"prepared", "complete"})
                            or (phase == "manifest" and path.name == "backup-manifest.json")):
                        failed.append(True)
                        raise OSError(primary)
                    return original(path, value, **kwargs)
                with patch.object(backup, "write_json", side_effect=persistent_failure):
                    with self.assertRaisesRegex(backup.BackupError, primary) as failure:
                        backup.copy_snapshot(self.root, destination, plan, minimum_free_bytes=0)
                self.assertEqual(str(failure.exception.__cause__), primary)
                self.assertTrue(failed)
                self.assertTrue(diagnostic_failures)
                self.assertFalse((destination / "backup-manifest.json").exists())
                state = json.loads((destination / "backup-state.json").read_text())
                self.assertEqual(state["copyStatus"], "incomplete" if phase == "state" else "prepared")
                self.assertNotIn("manifestSha256", state)
                with self.assertRaises(backup.BackupError):
                    backup.verify_snapshot(destination)
                for name in self.paths:
                    self.assertEqual((destination / "files" / name).read_bytes(), (self.root / name).read_bytes())
                self.assertEqual({p.relative_to(earlier).as_posix(): p.read_bytes() for p in earlier.rglob("*") if p.is_file()}, prior)
                self.assertEqual(backup.verify_snapshot(earlier)["verifiedFiles"], len(self.paths))

    def test_mutable_json_publication_flushes_the_owned_parent_directory(self):
        target = self.base / "state.json"
        original = backup.os.fsync
        flushed = []
        def tracked_fsync(descriptor):
            info = os.fstat(descriptor)
            flushed.append((info.st_dev, info.st_ino))
            return original(descriptor)
        with patch.object(backup.os, "fsync", side_effect=tracked_fsync):
            backup.write_json(target, {"status": "prepared"})
        parent = self.base.stat()
        self.assertIn((parent.st_dev, parent.st_ino), flushed)

    def test_normal_manifest_publication_rejects_changed_expected_payload(self):
        original = backup.write_json
        changed = []
        def change_published_payload(path, value, **kwargs):
            result = original(path, value, **kwargs)
            if path.name == "backup-manifest.json":
                altered = dict(value, backupId="different-backup")
                path.write_bytes(backup.encoded(backup.signed(altered, "manifestSha256")))
                changed.append(True)
            return result
        with patch.object(backup, "write_json", side_effect=change_published_payload):
            with self.assertRaisesRegex(backup.BackupError, "incomplete|identity|payload|changed"):
                self.run_copy()
        self.assertTrue(changed)
        self.assertEqual(json.loads((self.destination / "backup-state.json").read_text())["copyStatus"], "incomplete")
        self.assertEqual(json.loads((self.destination / "backup-manifest.json").read_text())["backupId"], "different-backup")
        for name in self.paths:
            self.assertEqual((self.destination / "files" / name).read_bytes(), (self.root / name).read_bytes())

    def test_manifest_directory_fsync_error_reconciles_exact_owned_bytes_with_diagnostic(self):
        original = backup.os.fsync
        failed = []
        def fail_marker_parent_fsync(descriptor):
            info = os.fstat(descriptor)
            if self.destination.exists():
                parent = self.destination.stat()
                if ((info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino)
                        and (self.destination / "backup-manifest.json").exists() and not failed):
                    failed.append(True)
                    raise OSError("injected manifest parent fsync failure")
            return original(descriptor)
        with patch.object(backup.os, "fsync", side_effect=fail_marker_parent_fsync):
            result = self.run_copy()
        self.assertTrue(failed)
        self.assertEqual(result["copyStatus"], "complete")
        self.assertEqual(result.get("completionPublication"),
                         {"status": "exact-readback-after-write-error", "durabilityVerified": False})
        stored = json.loads((self.destination / "backup-manifest.json").read_text())
        self.assertNotIn("completionPublication", stored)
        self.assertEqual(backup.verify_snapshot(self.destination)["manifestSha256"], stored["manifestSha256"])
        self.assertEqual(json.loads((self.destination / "backup-state.json").read_text())["copyStatus"], "prepared")

    def test_written_marker_flush_file_fsync_and_close_errors_reconcile_owned_bytes(self):
        original_fdopen, original_fsync = backup.os.fdopen, backup.os.fsync
        for phase in ["flush", "file-fsync", "close"]:
            with self.subTest(phase=phase):
                destination = self.destination.parent / ("lumen-late-marker-" + phase)
                plan = backup.prepare_fileset(self.root, self.paths, destination.name)
                marker = destination / "backup-manifest.json"
                failed = []
                def marker_descriptor(descriptor):
                    return marker.exists() and os.fstat(descriptor).st_ino == marker.stat().st_ino
                class LateWriter:
                    def __init__(self, stream):
                        self.stream = stream
                    def __enter__(self):
                        self.stream.__enter__()
                        return self
                    def __exit__(self, *args):
                        result = self.stream.__exit__(*args)
                        if phase == "close" and not failed:
                            failed.append(True)
                            raise OSError("injected completed marker close failure")
                        return result
                    def __getattr__(self, name):
                        return getattr(self.stream, name)
                    def flush(self):
                        result = self.stream.flush()
                        if phase == "flush" and not failed:
                            failed.append(True)
                            raise OSError("injected completed marker flush failure")
                        return result
                def wrapped_writer(descriptor, mode, *args, **kwargs):
                    stream = original_fdopen(descriptor, mode, *args, **kwargs)
                    return LateWriter(stream) if mode == "wb" and marker_descriptor(descriptor) else stream
                def failing_fsync(descriptor):
                    if phase == "file-fsync" and marker_descriptor(descriptor) and not failed:
                        failed.append(True)
                        raise OSError("injected completed marker file fsync failure")
                    return original_fsync(descriptor)
                with patch.object(backup.os, "fdopen", side_effect=wrapped_writer), \
                     patch.object(backup.os, "fsync", side_effect=failing_fsync):
                    result = backup.copy_snapshot(self.root, destination, plan, minimum_free_bytes=0)
                self.assertTrue(failed)
                self.assertEqual(result.get("completionPublication"),
                                 {"status": "exact-readback-after-write-error", "durabilityVerified": False})
                stored = json.loads(marker.read_text())
                self.assertNotIn("completionPublication", stored)
                self.assertEqual(backup.verify_snapshot(destination)["manifestSha256"], stored["manifestSha256"])

    def test_short_completion_marker_write_keeps_partial_bytes_without_success(self):
        original_fdopen = backup.os.fdopen
        marker = self.destination / "backup-manifest.json"
        partial = []
        class ShortWriter:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def write(self, payload):
                partial.append(payload[:len(payload) // 2])
                return self.stream.write(partial[-1])
        def wrapped_writer(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            is_marker = marker.exists() and os.fstat(descriptor).st_ino == marker.stat().st_ino
            return ShortWriter(stream) if mode == "wb" and is_marker else stream
        with patch.object(backup.os, "fdopen", side_effect=wrapped_writer):
            with self.assertRaisesRegex(backup.BackupError, "Incomplete.*metadata write"):
                self.run_copy()
        self.assertEqual(marker.read_bytes(), partial[0])
        self.assertEqual(json.loads((self.destination / "backup-state.json").read_text())["copyStatus"], "incomplete")
        with self.assertRaises(backup.BackupError):
            backup.verify_snapshot(self.destination)

    def test_copy_rejects_output_mutation_during_successful_file_fsync(self):
        selected = sorted(self.paths)[0]
        output = self.destination / "files" / selected
        original = backup.os.fsync
        changed = []
        def mutate_during_fsync(descriptor):
            result = original(descriptor)
            if output.exists() and os.fstat(descriptor).st_ino == output.stat().st_ino and not changed:
                before = output.stat()
                output.write_bytes(b"x" * before.st_size)
                os.utime(output, ns=(before.st_atime_ns, before.st_mtime_ns))
                changed.append(True)
            return result
        with patch.object(backup.os, "fsync", side_effect=mutate_during_fsync):
            with self.assertRaisesRegex(backup.BackupError, "identity|changed"):
                self.run_copy()
        self.assertTrue(changed)
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        self.assertEqual(json.loads((self.destination / "backup-state.json").read_text())["copyStatus"], "incomplete")

    def test_copy_rejects_output_mutation_during_successful_flush(self):
        selected = sorted(self.paths)[0]
        output = self.destination / "files" / selected
        original_fdopen = backup.os.fdopen
        changed = []
        class MutatingWriter:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def flush(self):
                result = self.stream.flush()
                if output.stat().st_size and not changed:
                    before = output.stat()
                    output.write_bytes(b"x" * before.st_size)
                    os.utime(output, ns=(before.st_atime_ns, before.st_mtime_ns))
                    changed.append(True)
                return result
        def wrapped_writer(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            matches = output.exists() and os.fstat(descriptor).st_ino == output.stat().st_ino
            return MutatingWriter(stream) if mode == "wb" and matches else stream
        with patch.object(backup.os, "fdopen", side_effect=wrapped_writer):
            with self.assertRaisesRegex(backup.BackupError, "identity|changed"):
                self.run_copy()
        self.assertTrue(changed)
        self.assertFalse((self.destination / "backup-manifest.json").exists())

    def test_copy_rejects_short_destination_write_before_manifest_publication(self):
        selected = sorted(self.paths)[0]
        output = self.destination / "files" / selected
        original_fdopen = backup.os.fdopen
        written = []
        class ShortWriter:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def write(self, payload):
                written.append(payload[:-1])
                return self.stream.write(written[-1])
        def wrapped_writer(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            matches = output.exists() and os.fstat(descriptor).st_ino == output.stat().st_ino
            return ShortWriter(stream) if mode == "wb" and matches else stream
        with patch.object(backup.os, "fdopen", side_effect=wrapped_writer):
            with self.assertRaisesRegex(backup.BackupError, "Incomplete.*write"):
                self.run_copy()
        self.assertEqual(output.read_bytes(), written[0])
        self.assertFalse((self.destination / "backup-manifest.json").exists())

    def test_unexpected_preexisting_completion_marker_is_never_overwritten_or_rescued(self):
        original = backup.write_json
        older = b"retained pre-existing marker\n"
        inserted = []
        def insert_before_manifest(path, value, **kwargs):
            if path.name == "backup-manifest.json":
                path.write_bytes(older)
                inserted.append(True)
            return original(path, value, **kwargs)
        with patch.object(backup, "write_json", side_effect=insert_before_manifest):
            with self.assertRaisesRegex(backup.BackupError, "incomplete|exists"):
                self.run_copy()
        self.assertTrue(inserted)
        self.assertEqual((self.destination / "backup-manifest.json").read_bytes(), older)
        self.assertEqual(json.loads((self.destination / "backup-state.json").read_text())["copyStatus"], "incomplete")

    def test_late_manifest_error_cannot_rescue_identical_replacement_identity(self):
        original = backup.write_json
        changed = []
        def replace_then_fail(path, value, **kwargs):
            result = original(path, value, **kwargs)
            if path.name == "backup-manifest.json":
                raw = path.read_bytes()
                path.rename(path.with_name("retained-owned-marker.json"))
                path.write_bytes(raw)
                changed.append(True)
                raise OSError("primary publication cleanup failure")
            return result
        with patch.object(backup, "write_json", side_effect=replace_then_fail):
            with self.assertRaisesRegex(backup.BackupError, "primary publication cleanup failure") as failure:
                self.run_copy()
        self.assertTrue(changed)
        self.assertEqual(str(failure.exception.__cause__), "primary publication cleanup failure")
        self.assertEqual((self.destination / "backup-manifest.json").read_bytes(),
                         (self.destination / "retained-owned-marker.json").read_bytes())

    def test_manifest_publication_rejects_displaced_destination_on_normal_and_error_routes(self):
        for late_error in [False, True]:
            with self.subTest(late_error=late_error):
                destination = self.destination.parent / ("lumen-publication-race-" + str(late_error))
                displaced = self.destination.parent / (destination.name + "-retained")
                plan = backup.prepare_fileset(self.root, self.paths, destination.name)
                original = backup.write_json
                def move_after_manifest(path, value, **kwargs):
                    result = original(path, value, **kwargs)
                    if path.name == "backup-manifest.json":
                        destination.rename(displaced)
                        destination.mkdir(mode=0o700)
                        (destination / "backup-manifest.json").write_bytes(backup.encoded(value))
                        if late_error:
                            raise OSError("primary directory move publication failure")
                    return result
                with patch.object(backup, "write_json", side_effect=move_after_manifest):
                    with self.assertRaises(backup.BackupError):
                        backup.copy_snapshot(self.root, destination, plan, minimum_free_bytes=0)
                self.assertFalse((destination / "files").exists())
                self.assertEqual(backup.verify_snapshot(displaced)["verifiedFiles"], len(self.paths))
                for name in self.paths:
                    self.assertEqual((displaced / "files" / name).read_bytes(), (self.root / name).read_bytes())

    def test_postpublication_wrapped_stat_io_reconciles_only_transient_owned_marker(self):
        original_stat = backup.os.stat
        for fault in ["transient", "persistent", "replacement"]:
            with self.subTest(fault=fault):
                destination = self.destination.parent / ("lumen-marker-stat-" + fault)
                plan = backup.prepare_fileset(self.root, self.paths, destination.name)
                marker = destination / "backup-manifest.json"
                failures = []
                def failing_named_stat(name, *args, **kwargs):
                    if kwargs.get("dir_fd") is not None and name == "backup-manifest.json" \
                            and (not failures or fault == "persistent"):
                        failures.append(True)
                        if fault == "replacement":
                            raw = marker.read_bytes()
                            marker.rename(marker.with_name("retained-owned-marker.json"))
                            marker.write_bytes(raw)
                        raise OSError("injected fully published marker stat IO failure")
                    return original_stat(name, *args, **kwargs)
                with patch.object(backup.os, "stat", side_effect=failing_named_stat):
                    if fault == "transient":
                        result = backup.copy_snapshot(self.root, destination, plan, minimum_free_bytes=0)
                        self.assertEqual(result["copyStatus"], "complete")
                        self.assertEqual(result.get("completionPublication"),
                                         {"status": "exact-readback-after-write-error", "durabilityVerified": False})
                        self.assertEqual(len(failures), 1)
                    else:
                        with self.assertRaisesRegex(backup.BackupError, "fully published marker stat IO failure"):
                            backup.copy_snapshot(self.root, destination, plan, minimum_free_bytes=0)
                self.assertEqual(len(failures), 2 if fault == "persistent" else 1)
                state = backup.read_json(destination / "backup-state.json")
                self.assertEqual(state["copyStatus"], "prepared" if fault == "transient" else "incomplete")
                self.assertNotIn("completionPublication", backup.read_json(marker))
                self.assertEqual(backup.verify_snapshot(destination)["verifiedFiles"], len(self.paths))
                if fault == "replacement":
                    self.assertEqual(marker.read_bytes(), (destination / "retained-owned-marker.json").read_bytes())

    def test_expected_production_hash_is_checked_during_copy(self):
        plan = self.plan(expected_hashes={self.paths[1]: "a" * 64})
        with self.assertRaisesRegex(backup.BackupError, "hash"):
            self.run_copy(plan)
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        state = json.loads((self.destination / "backup-state.json").read_text())
        self.assertEqual(state["copyStatus"], "incomplete")

    def test_existing_destination_is_never_overwritten(self):
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("earlier backup")
        with self.assertRaisesRegex(backup.BackupError, "exists"):
            self.run_copy()
        self.assertEqual(marker.read_text(), "earlier backup")

    def test_creds_traversal_absolute_and_duplicate_sources_reject_before_copy(self):
        for name in [".env", ".env.production", ".envrc", ".netrc", ".npmrc", "config/id_ed25519",
                     "config/private.pem", "config/credentials.json",
                     "Audiobook/v8/receipts/request.json", "../outside.txt", "/tmp/outside.txt"]:
            with self.subTest(name=name):
                with self.assertRaises(backup.BackupError):
                    backup.prepare_fileset(self.root, [name], "lumen-backup-fixture")
        with self.assertRaisesRegex(backup.BackupError, "Duplicate"):
            backup.prepare_fileset(self.root, [self.paths[0], self.paths[0]], "lumen-backup-fixture")
        self.assertFalse(self.destination.exists())

    def test_external_or_internal_symlink_source_rejects(self):
        for target in [self.root / self.paths[0], self.base / "outside.txt"]:
            target.write_text("source")
            link = self.root / "linked.md"
            link.symlink_to(target)
            with self.assertRaisesRegex(backup.BackupError, "symlink"):
                backup.prepare_fileset(self.root, ["linked.md"], "lumen-backup-fixture")
            link.unlink()

    def test_common_oauth_credentials_are_rejected_even_when_explicitly_selected(self):
        for name in ["token.json", "refresh_token.json", "client_secret_lumen.json",
                     "web/client_secrets.json", "web/oauth-token.json", "web/id_token.json",
                     "secret.json", "docs/Secrets.json"]:
            with self.subTest(name=name):
                source = self.root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text('{"fixture":"secret"}')
                with self.assertRaisesRegex(backup.BackupError, "Excluded credential"):
                    backup.prepare_fileset(self.root, [name], "lumen-backup-fixture")

    def test_credential_bearing_parent_components_reject_explicit_sources(self):
        for name in ["web/secrets/google.json", "docs/credentials/config.json",
                     "Audiobook/v8/client_secrets/config.json", "Audiobook/v7/.oauth/provider.json",
                     "Audiobook/author-audit/.Secrets/config.json", "web/private_key/source.json",
                     "web/private_keys/config.json", "docs/api_keys/config.json",
                     "web/PRIVATE-KEYS/config.json", "docs/API-KEYS/config.json",
                     "web/service_accounts/google.json", "docs/SERVICE-ACCOUNTS/config.json"]:
            with self.subTest(name=name):
                source = self.root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text('{"fixture":"credential-directory"}')
                with self.assertRaisesRegex(backup.BackupError, "Excluded credential"):
                    backup.prepare_fileset(self.root, [name], "lumen-backup-fixture")
        self.assertFalse(self.destination.exists())

    def test_camelcase_and_compact_credential_components_reject_explicit_sources(self):
        for name in ["web/serviceAccountKey.json", "privateKey.json", "apiKey.json",
                     "web/APIKey.json", "docs/privateKEY.json", "web/SERVICEAccountKey.json",
                     "web/clientSecret.json", "docs/accessToken.json", "web/MyAPIKeys.json",
                     "web/serviceAccountKey/config.json", "docs/.APIKeys/config.json",
                     "web/APIKEY.json", "docs/privatekey/config.json",
                     "web/SERVICEACCOUNTKEY.json", "docs/serviceaccountkey/config.json",
                     "web/.OAuth/config.json"]:
            with self.subTest(name=name):
                source = self.root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text('{"fixture":"camelcase-credential"}')
                with self.assertRaisesRegex(backup.BackupError, "Excluded credential"):
                    backup.prepare_fileset(self.root, [name], "lumen-backup-fixture")
        self.assertFalse(self.destination.exists())

    def test_credential_normalization_keeps_innocent_words_and_original_source_paths(self):
        paths = ["web/APIKeyboard.js", "web/privateKeynote.js", "docs/serviceAccountability.md",
                 "web/accessTokenize.js", "docs/authTokenization.md", "web/OpenAIResponse.js",
                 "web/server/private-access.cjs"]
        for name in paths:
            source = self.root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("innocent source fixture")
        plan = backup.prepare_fileset(self.root, paths, "lumen-backup-fixture")
        self.assertEqual([row["path"] for row in plan["files"]], sorted(paths))
        sys.path.insert(0, str(SCRIPT.parent))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPT.parent)))
        import book_content as content
        with patch.object(content, "load_project_registry", return_value={}), \
             patch.object(content, "load_recording_inventory", return_value={"recordings": []}):
            collected = backup.collect_release_fileset(self.root, "lumen-backup-fixture")
        self.assertTrue(set(paths).issubset(row["path"] for row in collected["files"]))

    def test_apple_p8_keys_reject_explicit_and_production_sources_and_skip_generic_walks(self):
        keys = ["web/AuthKey_ABC123.p8", "docs/signing.P8",
                "Audiobook/author-audit/signing.p8", "Audiobook/v8/local-checks/AuthKey_ABC123.P8"]
        innocent = ["web/AuthKey_example.md", "docs/Appendix-p8.md", "web/auth-keyboard.js"]
        for name in keys + innocent:
            source = self.root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"disposable fixture")
        for name in keys:
            with self.subTest(explicit=name):
                with self.assertRaisesRegex(backup.BackupError, "Excluded credential"):
                    backup.prepare_fileset(self.root, [name], "lumen-backup-fixture")
        sys.path.insert(0, str(SCRIPT.parent))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPT.parent)))
        import book_content as content
        with patch.object(content, "load_project_registry", return_value={}), \
             patch.object(content, "load_recording_inventory", return_value={"recordings": []}):
            collected = backup.collect_release_fileset(self.root, "lumen-backup-fixture")
        names = {row["path"] for row in collected["files"]}
        mp3 = self.root / self.paths[2]
        lossless = mp3.with_suffix(".wav"); lossless.write_bytes(b"lossless fixture")
        mp3.with_suffix(".checkpoint.json").write_text(json.dumps({
            "output_sha256": {"lossless_wav": hashlib.sha256(lossless.read_bytes()).hexdigest()}}))
        mp3.with_suffix(".qa.json").write_text('{"warnings":[]}')
        for recording in [
            {"sourcePath": keys[0], "sha256": "a" * 64, "selectedClips": []},
            {"sourcePath": self.paths[2], "sha256": hashlib.sha256(mp3.read_bytes()).hexdigest(),
             "selectedClips": [{"sourcePath": keys[-1], "rawAudioSha256": "a" * 64}]},
        ]:
            with self.subTest(selected_source=recording):
                with patch.object(content, "load_project_registry", return_value={}), \
                     patch.object(content, "load_recording_inventory", return_value={"recordings": [recording]}):
                    with self.assertRaisesRegex(backup.BackupError, "Excluded credential"):
                        backup.collect_release_fileset(self.root, "lumen-backup-fixture")
        self.assertTrue(set(keys).isdisjoint(names))
        self.assertTrue(set(innocent).issubset(names))
        self.assertFalse(self.destination.exists())

    def test_changed_plan_source_clock_is_rejected_before_destination_creation(self):
        plan = self.plan()
        (self.root / self.paths[0]).write_text("source changed")
        with self.assertRaisesRegex(backup.BackupError, "changed"):
            self.run_copy(plan)
        self.assertFalse(self.destination.exists())

    def test_same_size_replacement_with_preserved_mtime_rejects_before_copy(self):
        plan = self.plan()
        source = self.root / self.paths[0]
        before = source.stat()
        replacement = source.with_name("replacement.md")
        replacement.write_bytes(b"x" * before.st_size)
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(source)
        self.assertEqual(source.stat().st_size, before.st_size)
        self.assertEqual(source.stat().st_mtime_ns, before.st_mtime_ns)
        with self.assertRaisesRegex(backup.BackupError, "identity|changed"):
            self.run_copy(plan)
        self.assertFalse(self.destination.exists())

    def test_same_size_in_place_change_with_preserved_mtime_rejects_before_copy(self):
        plan = self.plan()
        source = self.root / self.paths[0]
        before = source.stat()
        with source.open("r+b") as stream:
            stream.write(b"x" * before.st_size)
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(source.stat().st_ino, before.st_ino)
        self.assertEqual(source.stat().st_size, before.st_size)
        self.assertEqual(source.stat().st_mtime_ns, before.st_mtime_ns)
        with self.assertRaisesRegex(backup.BackupError, "identity|changed"):
            self.run_copy(plan)
        self.assertFalse(self.destination.exists())

    def test_same_clock_swap_between_validation_and_copy_cannot_write_replacement_bytes(self):
        plan = self.plan()
        original = backup.copy_hashed
        swapped = []
        def swap_before_open(source, destination, **kwargs):
            if not swapped:
                before = source.stat()
                replacement = source.with_name("replacement.md")
                replacement.write_bytes(b"x" * before.st_size)
                os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
                replacement.replace(source)
                swapped.append(destination)
            return original(source, destination, **kwargs)
        with patch.object(backup, "copy_hashed", side_effect=swap_before_open):
            with self.assertRaisesRegex(backup.BackupError, "identity|changed"):
                self.run_copy(plan)
        self.assertFalse(swapped[0].exists(), "Replacement bytes must not reach the destination")
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        self.assertEqual(json.loads((self.destination / "backup-state.json").read_text())["copyStatus"], "incomplete")

    def test_current_plans_bind_source_identity_and_old_plans_are_inspection_only(self):
        plan = self.plan()
        self.assertEqual(plan["schemaVersion"], 2)
        for row in plan["files"]:
            info = (self.root / row["path"]).stat()
            self.assertEqual(row["sourceIdentity"], {"device": info.st_dev, "inode": info.st_ino,
                                                    "ctimeNs": info.st_ctime_ns})
        historic = copy.deepcopy(plan)
        historic["schemaVersion"] = 1
        for row in historic["files"]:
            row.pop("sourceIdentity")
        historic = backup.signed(historic, "planSha256")
        self.assertEqual(len(backup.validate_fileset(historic)), len(self.paths))
        with self.assertRaisesRegex(backup.BackupError, "identity|fresh plan"):
            self.run_copy(historic)
        self.assertFalse(self.destination.exists())

    def test_in_place_mutation_during_read_is_rejected_before_unreviewed_bytes_are_written(self):
        plan = self.plan()
        name = sorted(self.paths)[0]
        source = self.root / name
        before = source.stat()
        original_fdopen = backup.os.fdopen
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
        with patch.object(backup.os, "fdopen", side_effect=mutate_after_read):
            with self.assertRaisesRegex(backup.BackupError, "identity changed"):
                self.run_copy(plan)
        self.assertTrue(changed)
        self.assertEqual(source.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual((self.destination / "files" / name).read_bytes(), b"")
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        self.assertEqual(json.loads((self.destination / "backup-state.json").read_text())["copyStatus"], "incomplete")

    def test_plan_hash_and_byte_totals_cannot_be_silently_edited(self):
        for edit in [lambda p: p.update(totalBytes=1), lambda p: p["files"][0].update(bytes=1)]:
            plan = copy.deepcopy(self.plan()); edit(plan)
            with self.assertRaisesRegex(backup.BackupError, "digest|total"):
                self.run_copy(plan)
        self.assertFalse(self.destination.exists())

    def test_insufficient_disk_space_keeps_drive_untouched(self):
        with patch.object(backup.shutil, "disk_usage", return_value=type("Usage", (), {"free": 10})()):
            with self.assertRaisesRegex(backup.BackupError, "space"):
                backup.copy_snapshot(self.root, self.destination, self.plan(), minimum_free_bytes=3)
        self.assertFalse(self.destination.exists())

    def test_public_runtime_destination_and_symlink_parent_reject_before_copy(self):
        destination = self.root / "web/dist/backup"
        destination.parent.mkdir(parents=True)
        with self.assertRaisesRegex(backup.BackupError, "public"):
            backup.copy_snapshot(self.root, destination, self.plan(), minimum_free_bytes=0)
        real = self.base / "actual-drive"; real.mkdir()
        link = self.base / "linked-drive"; link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(backup.BackupError, "symlink"):
            backup.copy_snapshot(self.root, link / "snapshot", self.plan(), minimum_free_bytes=0)

    def test_restore_sample_verifies_new_restored_bytes(self):
        manifest = self.run_copy()
        restored = self.base / "restore"
        report = backup.restore_sample(self.root, self.destination, restored, [self.paths[0], self.paths[2]],
                                       minimum_free_bytes=0)
        self.assertEqual(report["verifiedFiles"], 2)
        self.assertEqual(report["manifestSha256"], manifest["manifestSha256"])
        self.assertEqual(report["verificationScope"], "local-restored-sample")
        for name in [self.paths[0], self.paths[2]]:
            self.assertEqual((restored / name).read_bytes(), (self.root / name).read_bytes())

    def test_verification_rejects_marker_identity_changes_during_file_hashes(self):
        manifest = self.run_copy()
        marker = self.destination / "backup-manifest.json"
        original = backup.file_digest
        for change in ["invalid-replacement", "identical-replacement", "same-mtime-in-place"]:
            with self.subTest(change=change):
                backup.write_json(marker, manifest)
                changed = []
                def mutate_marker(path):
                    result = original(path)
                    if not changed:
                        before, raw = marker.stat(), marker.read_bytes()
                        if change.endswith("replacement"):
                            retained = marker.with_name("retained-" + change + ".json")
                            marker.rename(retained)
                            marker.write_bytes(raw if change.startswith("identical") else b'{"invalid":true}\n')
                        else:
                            marker.write_bytes(raw)
                            os.utime(marker, ns=(before.st_atime_ns, before.st_mtime_ns))
                        changed.append(True)
                    return result
                with patch.object(backup, "file_digest", side_effect=mutate_marker):
                    with self.assertRaisesRegex(backup.BackupError, "manifest|metadata|identity|changed"):
                        backup.verify_snapshot(self.destination)
                self.assertTrue(changed)
                for name in self.paths:
                    self.assertEqual((self.destination / "files" / name).read_bytes(), (self.root / name).read_bytes())

    def test_verification_rechecks_earlier_hashed_file_identity_before_success(self):
        manifest = self.run_copy()
        first = self.destination / "files" / manifest["files"][0]["path"]
        last = self.destination / "files" / manifest["files"][-1]["path"]
        original = backup.file_digest
        changed = []
        def mutate_previous(path):
            result = original(path)
            if path == last and not changed:
                before = first.stat()
                first.write_bytes(b"x" * before.st_size)
                os.utime(first, ns=(before.st_atime_ns, before.st_mtime_ns))
                changed.append(True)
            return result
        with patch.object(backup, "file_digest", side_effect=mutate_previous):
            with self.assertRaisesRegex(backup.BackupError, "identity|changed"):
                backup.verify_snapshot(self.destination)
        self.assertTrue(changed)

    def test_restore_pins_manifest_through_output_copy_and_hash(self):
        manifest = self.run_copy()
        marker = self.destination / "backup-manifest.json"
        original = backup.file_digest
        for phase in ["copy", "hash"]:
            with self.subTest(phase=phase):
                backup.write_json(marker, manifest)
                restored = self.base / ("restore-marker-" + phase)
                changed = []
                def replace_marker():
                    raw = marker.read_bytes()
                    marker.rename(marker.with_name("retained-restore-" + phase + ".json"))
                    marker.write_bytes(raw)
                    changed.append(True)
                original_copy = backup.copy_hashed
                def copy_then_replace(source, destination, **kwargs):
                    result = original_copy(source, destination, **kwargs)
                    replace_marker()
                    return result
                def hash_then_replace(path):
                    result = original(path)
                    if restored in Path(path).parents and not changed:
                        replace_marker()
                    return result
                with patch.object(backup, "copy_hashed", side_effect=copy_then_replace if phase == "copy" else original_copy), \
                     patch.object(backup, "file_digest", side_effect=hash_then_replace):
                    with self.assertRaisesRegex(backup.BackupError, "manifest|metadata|identity|changed"):
                        backup.restore_sample(self.root, self.destination, restored, [self.paths[0]], minimum_free_bytes=0)
                self.assertTrue(changed)
                self.assertEqual((restored / self.paths[0]).read_bytes(), (self.root / self.paths[0]).read_bytes())

    def test_restore_rejects_displaced_logical_root_after_final_output_hash(self):
        self.run_copy()
        restored = self.base / "restore-root-race"
        displaced = self.base / "retained-restore-root"
        original = backup.file_digest
        moved = []
        def move_after_hash(path):
            result = original(path)
            if restored in Path(path).parents and not moved:
                restored.rename(displaced)
                restored.mkdir(mode=0o700)
                moved.append(True)
            return result
        with patch.object(backup, "file_digest", side_effect=move_after_hash):
            with self.assertRaisesRegex(backup.BackupError, "Restore|restore|identity|changed"):
                backup.restore_sample(self.root, self.destination, restored, [self.paths[0]], minimum_free_bytes=0)
        self.assertTrue(moved)
        self.assertEqual(list(restored.iterdir()), [])
        self.assertEqual((displaced / self.paths[0]).read_bytes(), (self.root / self.paths[0]).read_bytes())

    def test_restore_rejects_output_replacement_after_hash_even_with_identical_bytes(self):
        self.run_copy()
        restored = self.base / "restore-output-race"
        original = backup.file_digest
        replaced = []
        def replace_after_hash(path):
            result = original(path)
            if restored in Path(path).parents and not replaced:
                raw = path.read_bytes()
                path.rename(path.with_name("retained-original.md"))
                path.write_bytes(raw)
                replaced.append(True)
            return result
        with patch.object(backup, "file_digest", side_effect=replace_after_hash):
            with self.assertRaisesRegex(backup.BackupError, "identity|changed"):
                backup.restore_sample(self.root, self.destination, restored, [self.paths[0]], minimum_free_bytes=0)
        self.assertTrue(replaced)
        self.assertEqual((restored / self.paths[0]).read_bytes(), (self.root / self.paths[0]).read_bytes())

    def test_restore_rechecks_earlier_outputs_after_later_hash(self):
        self.run_copy()
        restored = self.base / "restore-earlier-output-race"
        selected = [self.paths[0], self.paths[2]]
        original = backup.file_digest
        changed = []
        def mutate_previous_output(path):
            result = original(path)
            if path == restored / selected[-1] and not changed:
                earlier = restored / selected[0]
                before = earlier.stat()
                earlier.write_bytes(b"x" * before.st_size)
                os.utime(earlier, ns=(before.st_atime_ns, before.st_mtime_ns))
                changed.append(True)
            return result
        with patch.object(backup, "file_digest", side_effect=mutate_previous_output):
            with self.assertRaisesRegex(backup.BackupError, "identity|changed"):
                backup.restore_sample(self.root, self.destination, restored, selected, minimum_free_bytes=0)
        self.assertTrue(changed)

    def test_restore_primary_copy_error_survives_concurrent_manifest_change(self):
        self.run_copy()
        restored = self.base / "restore-primary-failure"
        marker = self.destination / "backup-manifest.json"
        original = backup.copy_hashed
        def copy_then_fail(source, destination, **kwargs):
            original(source, destination, **kwargs)
            raw = marker.read_bytes()
            marker.rename(marker.with_name("retained-original-manifest.json"))
            marker.write_bytes(raw)
            raise OSError("primary injected copy failure")
        with patch.object(backup, "copy_hashed", side_effect=copy_then_fail):
            with self.assertRaisesRegex(backup.BackupError, "primary injected copy failure") as failure:
                backup.restore_sample(self.root, self.destination, restored, [self.paths[0]], minimum_free_bytes=0)
        self.assertEqual(str(failure.exception.__cause__), "primary injected copy failure")
        self.assertEqual((restored / self.paths[0]).read_bytes(), (self.root / self.paths[0]).read_bytes())

    def test_restore_primary_error_survives_pinned_marker_close_error(self):
        self.run_copy()
        restored = self.base / "restore-primary-close-failure"
        marker = self.destination / "backup-manifest.json"
        original_fdopen = backup.os.fdopen
        copy_failed = []
        class ClosingReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                self.stream.__exit__(*args)
                if copy_failed:
                    raise OSError("secondary pinned marker close failure")
            def __getattr__(self, name):
                return getattr(self.stream, name)
        def wrapped_reader(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            return ClosingReader(stream) if mode == "rb" and os.fstat(descriptor).st_ino == marker.stat().st_ino else stream
        def fail_copy(*args, **kwargs):
            copy_failed.append(True)
            raise OSError("primary restore copy failure")
        with patch.object(backup.os, "fdopen", side_effect=wrapped_reader), \
             patch.object(backup, "copy_hashed", side_effect=fail_copy):
            with self.assertRaisesRegex(backup.BackupError, "primary restore copy failure") as failure:
                backup.restore_sample(self.root, self.destination, restored, [self.paths[0]], minimum_free_bytes=0)
        self.assertEqual(str(failure.exception.__cause__), "primary restore copy failure")

    def test_primary_partial_marker_write_error_survives_stream_and_parent_close_errors(self):
        original_fdopen, original_close = backup.os.fdopen, backup.os.close
        marker = self.destination / "backup-manifest.json"
        partial, parent_failed = [], []
        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                self.stream.__exit__(*args)
                raise OSError("secondary marker stream close failure")
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def write(self, payload):
                partial.append(payload[:len(payload) // 2])
                self.stream.write(partial[-1])
                raise OSError("primary partial marker write failure")
        def wrapped_writer(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            matches = marker.exists() and os.fstat(descriptor).st_ino == marker.stat().st_ino
            return FailingWriter(stream) if mode == "wb" and matches else stream
        def failing_parent_close(descriptor):
            info = os.fstat(descriptor)
            is_parent = self.destination.exists() and info.st_ino == self.destination.stat().st_ino
            result = original_close(descriptor)
            if partial and is_parent and not parent_failed:
                parent_failed.append(True)
                raise OSError("secondary marker parent close failure")
            return result
        with patch.object(backup.os, "fdopen", side_effect=wrapped_writer), \
             patch.object(backup.os, "close", side_effect=failing_parent_close):
            with self.assertRaisesRegex(backup.BackupError, "primary partial marker write failure") as failure:
                self.run_copy()
        self.assertTrue(parent_failed)
        self.assertEqual(str(failure.exception.__cause__), "primary partial marker write failure")
        self.assertEqual(marker.read_bytes(), partial[0])

    def test_restore_cannot_use_replacement_manifest_after_verifying_another_manifest(self):
        manifest = self.run_copy()
        selected = self.paths[0]
        restored = self.base / "restore-manifest-race"
        original_verify = backup.verify_snapshot
        verified = []
        def verify_then_replace(snapshot, paths):
            report = original_verify(snapshot, paths)
            verified.append(report["manifestSha256"])
            replacement = b"b" * (self.root / selected).stat().st_size
            (self.destination / "files" / selected).write_bytes(replacement)
            changed = copy.deepcopy(manifest)
            next(row for row in changed["files"] if row["path"] == selected)["sha256"] = hashlib.sha256(replacement).hexdigest()
            backup.write_json(self.destination / "backup-manifest.json", backup.signed(changed, "manifestSha256"))
            return report
        with patch.object(backup, "verify_snapshot", side_effect=verify_then_replace):
            with self.assertRaisesRegex(backup.BackupError, "manifest.*changed|manifest.*differ"):
                backup.restore_sample(self.root, self.destination, restored, [selected], minimum_free_bytes=0)
        self.assertEqual(verified, [manifest["manifestSha256"]])
        self.assertFalse(restored.exists())

    def test_restore_default_reserve_rejects_insufficient_capacity_before_creation(self):
        self.run_copy()
        restored = self.base / "restore-default-reserve"
        sample_bytes = (self.root / self.paths[0]).stat().st_size
        usage = type("Usage", (), {"free": sample_bytes + backup.DEFAULT_RESERVE_BYTES - 1})()
        with patch.object(backup.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(backup.BackupError, "space"):
                backup.restore_sample(self.root, self.destination, restored, [self.paths[0]])
        self.assertFalse(restored.exists())

    def test_restore_capacity_counts_every_selected_file_and_reserve(self):
        self.run_copy()
        restored = self.base / "restore-selected-capacity"
        selected = [self.paths[0], self.paths[2]]
        total = sum((self.root / name).stat().st_size for name in selected)
        reserve = 17
        usage = type("Usage", (), {"free": total + reserve - 1})()
        with patch.object(backup.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(backup.BackupError, "space"):
                backup.restore_sample(self.root, self.destination, restored, selected,
                                      minimum_free_bytes=reserve)
        self.assertFalse(restored.exists())

    def test_restore_invalid_reserve_rejects_before_creation(self):
        self.run_copy()
        restored = self.base / "restore-invalid-reserve"
        for reserve in [-1, True, 1.5, "17", None]:
            with self.subTest(reserve=reserve):
                with self.assertRaisesRegex(backup.BackupError, "Invalid free-space reserve"):
                    backup.restore_sample(self.root, self.destination, restored, [self.paths[0]],
                                          minimum_free_bytes=reserve)
                self.assertFalse(restored.exists())

    def test_restore_cli_honors_explicit_reserve_and_zero_override(self):
        self.run_copy()
        restored = self.base / "restore-cli-reserve"
        sample_bytes = (self.root / self.paths[0]).stat().st_size
        usage = type("Usage", (), {"free": sample_bytes})()
        arguments = ["--root", str(self.root), "--restore-sample", str(self.destination),
                     "--destination", str(restored), "--path", self.paths[0], "--minimum-free-bytes"]
        with patch.object(backup.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(SystemExit) as failure:
                backup.main(arguments + ["1"])
            self.assertEqual(failure.exception.code, 2)
            self.assertFalse(restored.exists())
            self.assertEqual(backup.main(arguments + ["0"]), 0)
        self.assertEqual((restored / self.paths[0]).read_bytes(), (self.root / self.paths[0]).read_bytes())

    def test_restore_rechecks_remaining_sample_capacity_before_next_file(self):
        self.run_copy()
        manifest_before = (self.destination / "backup-manifest.json").read_bytes()
        selected = [self.paths[0], self.paths[2]]
        sizes = [(self.root / name).stat().st_size for name in selected]
        reserve = 17
        restored = self.base / "restore-space-race"
        original = backup.copy_hashed
        completed = []
        def copy_then_lose_space(source, destination, **kwargs):
            result = original(source, destination, **kwargs)
            completed.append(destination)
            return result
        def available(_):
            free = sum(sizes) + reserve if not completed else sizes[1] + reserve - 1
            return type("Usage", (), {"free": free})()
        with patch.object(backup.shutil, "disk_usage", side_effect=available), \
             patch.object(backup, "copy_hashed", side_effect=copy_then_lose_space):
            with self.assertRaisesRegex(backup.BackupError, "space.*incomplete"):
                backup.restore_sample(self.root, self.destination, restored, selected,
                                      minimum_free_bytes=reserve)
        self.assertEqual(completed, [restored / selected[0]])
        self.assertEqual((restored / selected[0]).read_bytes(), (self.root / selected[0]).read_bytes())
        self.assertFalse((restored / selected[1]).exists())
        self.assertEqual((self.destination / "backup-manifest.json").read_bytes(), manifest_before)

    def test_restore_rechecks_remaining_bytes_before_every_chunk(self):
        selected = [self.paths[0], self.paths[2]]
        (self.root / selected[0]).write_bytes(b"x" * (backup.CHUNK_BYTES * 2 + 17))
        self.run_copy()
        manifest_before = (self.destination / "backup-manifest.json").read_bytes()
        total = sum((self.root / name).stat().st_size for name in selected)
        reserve = 64
        restored = self.base / "restore-chunk-space"
        target = restored / selected[0]
        def available(_):
            written = target.stat().st_size if target.exists() else 0
            unrelated_usage = 1 if written >= backup.CHUNK_BYTES else 0
            return type("Usage", (), {"free": total + reserve - written - unrelated_usage})()
        with patch.object(backup.shutil, "disk_usage", side_effect=available):
            with self.assertRaisesRegex(backup.BackupError, "space.*incomplete"):
                backup.restore_sample(self.root, self.destination, restored, selected,
                                      minimum_free_bytes=reserve)
        self.assertEqual(target.stat().st_size, backup.CHUNK_BYTES)
        self.assertEqual(target.read_bytes(), b"x" * backup.CHUNK_BYTES)
        self.assertFalse((restored / selected[1]).exists())
        self.assertEqual((self.destination / "backup-manifest.json").read_bytes(), manifest_before)

    def test_restore_exact_capacity_preserves_default_reserve(self):
        self.run_copy()
        selected = [self.paths[0], self.paths[2]]
        total = sum((self.root / name).stat().st_size for name in selected)
        restored = self.base / "restore-exact-default-capacity"
        def available(_):
            written = sum((restored / name).stat().st_size for name in selected if (restored / name).exists())
            return type("Usage", (), {"free": total + backup.DEFAULT_RESERVE_BYTES - written})()
        with patch.object(backup.shutil, "disk_usage", side_effect=available):
            report = backup.restore_sample(self.root, self.destination, restored, selected)
            self.assertEqual(available(None).free, backup.DEFAULT_RESERVE_BYTES)
        self.assertEqual(report["verificationScope"], "local-restored-sample")
        for name in selected:
            self.assertEqual((restored / name).read_bytes(), (self.root / name).read_bytes())

    def test_restore_rejects_public_runtime_destination_before_creation(self):
        self.run_copy()
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        restored = public / "restored-private"
        with self.assertRaisesRegex(backup.BackupError, "public"):
            backup.restore_sample(self.root, self.destination, restored, [self.paths[0], self.paths[2]])
        self.assertFalse(restored.exists())

    def test_restore_cli_requires_explicit_project_root(self):
        self.run_copy()
        restored = self.base / "restore-cli"
        with self.assertRaises(SystemExit) as failure:
            backup.main(["--restore-sample", str(self.destination), "--destination", str(restored),
                         "--path", self.paths[0]])
        self.assertEqual(failure.exception.code, 2)
        self.assertFalse(restored.exists())

    def test_restore_cli_applies_selected_project_public_boundary(self):
        self.run_copy()
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        restored = public / "restore-cli"
        with self.assertRaises(SystemExit) as failure:
            backup.main(["--root", str(self.root), "--restore-sample", str(self.destination),
                         "--destination", str(restored), "--path", self.paths[0]])
        self.assertEqual(failure.exception.code, 2)
        self.assertFalse(restored.exists())

    def test_destination_ancestor_symlink_during_copy_cannot_leak_private_bytes(self):
        public = self.root / "web/dist"
        public.mkdir(parents=True)
        def redirect_later_destination(done, *_):
            if done == 1:
                link = self.destination / "files/Audiobook/v8/raw"
                link.symlink_to(public, target_is_directory=True)
        with self.assertRaises(backup.BackupError):
            backup.copy_snapshot(self.root, self.destination, self.plan(), minimum_free_bytes=0,
                                 progress=redirect_later_destination)
        self.assertEqual(list(public.iterdir()), [])
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        state = json.loads((self.destination / "backup-state.json").read_text())
        self.assertEqual(state["copyStatus"], "incomplete")

    def test_source_ancestor_symlink_at_open_cannot_copy_unselected_bytes(self):
        plan = self.plan()
        original = backup.copy_hashed
        redirected = []
        def redirect_source_after_clock_check(source, destination, **kwargs):
            if not redirected:
                outside = self.base / "unselected-private"
                outside.mkdir()
                (outside / source.name).write_bytes(b"s" * source.stat().st_size)
                source.parent.rename(source.parent.with_name(source.parent.name + "-saved"))
                source.parent.symlink_to(outside, target_is_directory=True)
                redirected.append(destination)
            return original(source, destination, **kwargs)
        with patch.object(backup, "copy_hashed", side_effect=redirect_source_after_clock_check):
            with self.assertRaises(backup.BackupError):
                self.run_copy(plan)
        self.assertFalse(redirected[0].exists(), "Unselected bytes must never reach the backup destination")
        self.assertFalse((self.destination / "backup-manifest.json").exists())

    def test_same_size_tampered_backup_cannot_restore_or_claim_verification(self):
        self.run_copy()
        path = self.destination / "files" / self.paths[0]
        path.write_bytes(b"x" * path.stat().st_size)
        restored = self.base / "restore"
        with self.assertRaisesRegex(backup.BackupError, "hash"):
            backup.restore_sample(self.root, self.destination, restored, [self.paths[0]])
        self.assertFalse(restored.exists())

    def test_restore_rejects_existing_destination_unknown_files_and_manifest_tamper(self):
        self.run_copy()
        restored = self.base / "restore"; restored.mkdir()
        with self.assertRaisesRegex(backup.BackupError, "exists"):
            backup.restore_sample(self.root, self.destination, restored, [self.paths[0]])
        with self.assertRaisesRegex(backup.BackupError, "Unknown"):
            backup.restore_sample(self.root, self.destination, self.base / "new-restore", ["unknown.txt"])
        path = self.destination / "backup-manifest.json"
        manifest = json.loads(path.read_text()); manifest["files"][0]["sha256"] = "f" * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(backup.BackupError, "digest"):
            backup.verify_snapshot(self.destination)

    def test_backup_file_symlink_cannot_supply_restore_bytes(self):
        self.run_copy()
        path = self.destination / "files" / self.paths[0]
        outside = self.base / "outside-copy.txt"; outside.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(outside)
        with self.assertRaisesRegex(backup.BackupError, "symlink"):
            backup.verify_snapshot(self.destination, [self.paths[0]])

    def test_earlier_source_edit_during_later_copy_keeps_snapshot_incomplete(self):
        plan = self.plan()
        original = backup.copy_hashed
        calls = []
        def changing_source(source, destination, **kwargs):
            result = original(source, destination, **kwargs)
            calls.append(source)
            if len(calls) == 2:
                calls[0].write_bytes(b"x" * calls[0].stat().st_size)
            return result
        with patch.object(backup, "copy_hashed", side_effect=changing_source):
            with self.assertRaisesRegex(backup.BackupError, "changed"):
                self.run_copy(plan)
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        state = json.loads((self.destination / "backup-state.json").read_text())
        self.assertEqual(state["copyStatus"], "incomplete")

    def test_growing_source_never_writes_past_planned_bytes_or_consumes_reserve(self):
        name = sorted(self.paths)[0]
        source = self.root / name
        original_bytes = b"a" * (backup.CHUNK_BYTES * 2 + 17)
        source.write_bytes(original_bytes)
        plan = self.plan()
        planned_bytes = next(row["bytes"] for row in plan["files"] if row["path"] == name)
        reserve = backup.CHUNK_BYTES // 2
        initial_free = plan["totalBytes"] + reserve
        original_fdopen = backup.os.fdopen
        source_inode = source.stat().st_ino
        growth = b"g" * (backup.CHUNK_BYTES * 3)
        appended = []

        class GrowingReader:
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
                if not appended:
                    with source.open("ab") as output:
                        output.write(growth)
                    appended.append(True)
                return block

        def growing_fdopen(descriptor, mode, *args, **kwargs):
            stream = original_fdopen(descriptor, mode, *args, **kwargs)
            return GrowingReader(stream) if mode == "rb" and os.fstat(descriptor).st_ino == source_inode else stream

        def disk_space(_):
            copied = sum(path.stat().st_size for path in (self.destination / "files").rglob("*") if path.is_file())
            return type("Usage", (), {"free": initial_free - copied})()

        with patch.object(backup.os, "fdopen", side_effect=growing_fdopen), \
             patch.object(backup.shutil, "disk_usage", side_effect=disk_space):
            with self.assertRaisesRegex(backup.BackupError, "grew|byte count changed|identity changed"):
                backup.copy_snapshot(self.root, self.destination, plan, minimum_free_bytes=reserve)
        target = self.destination / "files" / name
        self.assertTrue(appended)
        self.assertLessEqual(target.stat().st_size, planned_bytes)
        self.assertEqual(target.read_bytes(), original_bytes[:target.stat().st_size])
        self.assertGreaterEqual(disk_space(self.destination).free, reserve)
        self.assertEqual(source.stat().st_size, planned_bytes + len(growth))
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        state = json.loads((self.destination / "backup-state.json").read_text())
        self.assertEqual(state["copyStatus"], "incomplete")
        self.assertEqual(state["copiedBytes"], 0)

    def test_backup_rechecks_remaining_capacity_after_each_flushed_chunk(self):
        selected = self.paths[0]
        source = self.root / selected
        source.write_bytes(b"x" * (backup.CHUNK_BYTES * 2 + 17))
        plan = backup.prepare_fileset(self.root, [selected], "lumen-backup-fixture")
        reserve = 64
        target = self.destination / "files" / selected
        def available(_):
            written = target.stat().st_size if target.exists() else 0
            unrelated_usage = 1 if written >= backup.CHUNK_BYTES else 0
            return type("Usage", (), {"free": plan["totalBytes"] + reserve - written - unrelated_usage})()
        with patch.object(backup.shutil, "disk_usage", side_effect=available):
            with self.assertRaisesRegex(backup.BackupError, "space.*incomplete"):
                backup.copy_snapshot(self.root, self.destination, plan, minimum_free_bytes=reserve)
        self.assertEqual(target.stat().st_size, backup.CHUNK_BYTES)
        self.assertEqual(target.read_bytes(), b"x" * backup.CHUNK_BYTES)
        self.assertEqual(source.stat().st_size, plan["totalBytes"])
        self.assertGreaterEqual(available(None).free, reserve)
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        state = json.loads((self.destination / "backup-state.json").read_text())
        self.assertEqual(state["copyStatus"], "incomplete")
        self.assertEqual(state["copiedBytes"], 0)
        self.assertEqual(state["files"], [])

    def test_backup_exact_capacity_preserves_reserve_across_chunks_and_files(self):
        selected = self.paths[0]
        (self.root / selected).write_bytes(b"x" * (backup.CHUNK_BYTES * 2 + 17))
        plan = self.plan()
        reserve = 64
        def available(_):
            written = sum(path.stat().st_size for path in (self.destination / "files").rglob("*") if path.is_file())
            return type("Usage", (), {"free": plan["totalBytes"] + reserve - written})()
        with patch.object(backup.shutil, "disk_usage", side_effect=available):
            manifest = backup.copy_snapshot(self.root, self.destination, plan, minimum_free_bytes=reserve)
        self.assertEqual(manifest["copyStatus"], "complete")
        self.assertEqual(available(None).free, reserve)
        self.assertEqual(backup.verify_snapshot(self.destination)["verifiedBytes"], plan["totalBytes"])

    def test_empty_planned_source_growth_is_detected_without_writing_extra_bytes(self):
        name = sorted(self.paths)[0]
        source = self.root / name
        source.write_bytes(b"")
        plan = self.plan()
        original = backup.copy_hashed
        def grow_before_copy(incoming, destination, **kwargs):
            if incoming == source:
                incoming.write_bytes(b"unexpected growth")
            return original(incoming, destination, **kwargs)
        with patch.object(backup, "copy_hashed", side_effect=grow_before_copy):
            with self.assertRaisesRegex(backup.BackupError, "grew|byte count changed|identity changed"):
                self.run_copy(plan)
        target = self.destination / "files" / name
        self.assertTrue(not target.exists() or target.stat().st_size == 0)
        self.assertFalse((self.destination / "backup-manifest.json").exists())
        self.assertEqual(json.loads((self.destination / "backup-state.json").read_text())["copyStatus"], "incomplete")

    def test_direct_file_copy_does_not_follow_a_last_moment_source_symlink(self):
        source = self.root / "last-moment-link.md"
        source.symlink_to(self.root / self.paths[0])
        with self.assertRaises(OSError):
            backup.copy_hashed(source, self.base / "copied.md")
        self.assertFalse((self.base / "copied.md").exists())

    def test_fifo_swap_after_validation_is_rejected_by_all_source_readers_without_hanging(self):
        # A real FIFO with no writer would block a plain O_RDONLY open. Each
        # subprocess is bounded and killed on timeout, so the test itself is safe.
        script = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import backup_release as backup
payload = json.loads(sys.stdin.read())
root = Path(payload["root"])
destination = Path(payload["destination"])
source = root / payload["path"]
original = backup.copy_hashed
def replace_with_fifo():
    source.unlink()
    os.mkfifo(source, 0o600)
def swap_before_copy(incoming, outgoing, **kwargs):
    if incoming == source:
        replace_with_fifo()
    return original(incoming, outgoing, **kwargs)
try:
    if payload["reader"] == "copy":
        backup.copy_hashed = swap_before_copy
        backup.copy_snapshot(root, destination, payload["plan"], minimum_free_bytes=0)
    else:
        backup.owned_file(root, payload["path"])
        replace_with_fifo()
        {"hash": backup.file_digest, "clock": backup.source_clock, "json": backup.read_json}[payload["reader"]](source)
except backup.BackupError as exc:
    print(json.dumps({"rejected": True, "error": str(exc)}))
else:
    raise RuntimeError("FIFO source was not rejected")
'''
        for reader in ["copy", "hash", "clock", "json"]:
            with self.subTest(reader=reader):
                source = self.root / sorted(self.paths)[0]
                if source.exists():
                    source.unlink()
                source.write_bytes(b"regular planned source")
                destination = self.destination.with_name("lumen-backup-fixture-" + reader)
                plan = backup.prepare_fileset(self.root, self.paths, destination.name)
                payload = {"root": str(self.root), "destination": str(destination), "path": source.relative_to(self.root).as_posix(),
                           "reader": reader, "plan": plan}
                try:
                    result = subprocess.run([sys.executable, "-c", script, str(SCRIPT.parent)],
                                            input=json.dumps(payload), capture_output=True, text=True, timeout=2)
                except subprocess.TimeoutExpired:
                    self.fail(f"{reader} blocked opening a FIFO after source validation")
                self.assertEqual(result.returncode, 0, result.stderr)
                response = json.loads(result.stdout)
                self.assertTrue(response["rejected"])
                self.assertIn("regular file", response["error"])
                if reader == "copy":
                    self.assertFalse((destination / "files" / payload["path"]).exists())
                    self.assertFalse((destination / "backup-manifest.json").exists())
                    self.assertEqual(json.loads((destination / "backup-state.json").read_text())["copyStatus"], "incomplete")

    def test_plan_cli_keeps_fileset_outside_public_runtime(self):
        public = self.root / "web/dist/backup-plan.json"
        with self.assertRaises(SystemExit) as failure:
            backup.main(["--root", str(self.root), "--plan", str(public), "--backup-id", "lumen-backup-fixture"])
        self.assertEqual(failure.exception.code, 2)
        self.assertFalse(public.exists())

    def test_collector_keeps_selected_audio_metadata_and_excludes_redundant_or_secret_files(self):
        sys.path.insert(0, str(SCRIPT.parent))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPT.parent)))
        import book_content as content
        raw = self.root / self.paths[1]
        mp3 = self.root / self.paths[2]
        lossless = mp3.with_suffix(".wav"); lossless.write_bytes(b"lossless fixture")
        raw.with_suffix(".json").write_text('{"status":"completed"}')
        checkpoint = {"output_sha256": {"lossless_wav": hashlib.sha256(lossless.read_bytes()).hexdigest()}}
        mp3.with_suffix(".checkpoint.json").write_text(json.dumps(checkpoint))
        mp3.with_suffix(".qa.json").write_text('{"warnings":[]}')
        excluded = ["Audiobook/v8/receipts/secret.json", "Audiobook/v8/delivery/redundant.m4b",
                     "Audiobook/v8/delivery/redundant.zip", "web/.env.local", "web/node_modules/module.js",
                     "web/.npmrc", "Audiobook/v8/raw/unselected.wav", "token.json", "refresh_token.json",
                     "client_secret_lumen.json", "web/client_secrets.json", "web/env/site.py",
                     "web/models/weights.bin", "docs/cache/proof.json", "Audiobook/author-audit/env/config.py",
                     "web/.cache/weights.bin", "web/.pytest_cache/cache.json", "web/downloaded_models/weights.bin",
                     "web/.mypy_cache/meta.json", "web/.ruff_cache/meta.json", "web/.tox/site.py", "web/.nox/site.py",
                     "secret.json", "secrets.json", "docs/Secrets.json", "web/secret.json",
                     "Audiobook/v8/secret.json", "Audiobook/v8/mastered/secrets.json",
                     "web/secrets/google.json", "docs/credentials/config.json",
                     "Audiobook/author-audit/.Secrets/config.json",
                     "Audiobook/v7/local-checks/credentials/config.json",
                     "Audiobook/v8/local-checks/secrets/google.json",
                     "web/art-direction/secrets/config.json",
                     "web/private_keys/config.json", "docs/api_keys/config.json",
                     "web/PRIVATE-KEYS/config.json", "docs/API-KEYS/config.json",
                     "web/service_accounts/google.json", "docs/SERVICE-ACCOUNTS/config.json",
                     "serviceAccountKey.json", "privateKey.json", "apiKey.json",
                     "web/serviceAccountKey.json", "web/APIKey.json",
                     "docs/.APIKeys/config.json", "web/serviceAccountKey/config.json",
                     "Audiobook/v7/serviceAccountKey.json", "Audiobook/v8/privateKey.json",
                     "Audiobook/v7/mastered/APIKey.json", "Audiobook/v8/delivery/MyAPIKeys.json"]
        for name in excluded:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("excluded")
        (self.root / "web/env/linked.py").symlink_to(self.root / self.paths[0])
        (self.root / "web/.cache/linked.py").symlink_to(self.root / self.paths[0])
        cast = self.root / "web/art-direction/models/portrait.png"
        cast.parent.mkdir(parents=True); cast.write_bytes(b"approved portrait fixture")
        docs = self.root / "docs/backlog/roadmap.md"; docs.parent.mkdir(parents=True); docs.write_text("restore me")
        inventory = {"recordings": [{"sourcePath": self.paths[2], "sha256": hashlib.sha256(mp3.read_bytes()).hexdigest(),
                     "selectedClips": [{"sourcePath": self.paths[1], "rawAudioSha256": hashlib.sha256(raw.read_bytes()).hexdigest()}]}]}
        with patch.object(content, "load_project_registry", return_value={}), patch.object(content, "load_recording_inventory", return_value=inventory):
            plan = backup.collect_release_fileset(self.root, "lumen-backup-fixture")
        names = [row["path"] for row in plan["files"]]
        self.assertTrue(set(self.paths).issubset(names))
        self.assertIn("Audiobook/v8/mastered/chapter-001.wav", names)
        self.assertIn("Audiobook/v8/raw/chapter-001-001.json", names)
        self.assertIn("docs/backlog/roadmap.md", names)
        self.assertIn("web/art-direction/models/portrait.png", names)
        self.assertTrue(set(excluded).isdisjoint(names))
        self.assertFalse(any(name.endswith((".m4b", ".zip")) or "receipts" in name or "unselected" in name or ".env" in name or "node_modules" in name or ".npmrc" in name for name in names))
        self.assertEqual(sum("expectedSha256" in row for row in plan["files"]), 3)

    def test_art_direction_exempts_only_exact_model_sources_and_skips_nested_caches(self):
        sys.path.insert(0, str(SCRIPT.parent))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPT.parent)))
        import book_content as content
        references = ["web/art-direction/models/adrian-marsh-v1.png",
                      "web/art-direction/models/daniel-yoon-v1.png",
                      "web/art-direction/approved-cast-v1/images/adrian-marsh-portrait-v1.webp"]
        generated = ["web/art-direction/downloaded_models/weights.bin",
                     "web/art-direction/.cache/weights.bin", "web/art-direction/.pytest_cache/data.json",
                     "web/art-direction/models/.cache/weights.bin",
                     "web/art-direction/models/downloaded_models/weights.bin",
                     "web/art-direction/research/models/weights.bin"]
        for name in references + generated:
            source = self.root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"fixture")
        link = self.root / "web/art-direction/.cache/linked.py"
        link.symlink_to(self.root / self.paths[0])
        with patch.object(content, "load_project_registry", return_value={}), \
             patch.object(content, "load_recording_inventory", return_value={"recordings": []}):
            plan = backup.collect_release_fileset(self.root, "lumen-backup-fixture")
        names = {row["path"] for row in plan["files"]}
        self.assertTrue(set(references).issubset(names))
        self.assertTrue(set(generated).isdisjoint(names))
        self.assertNotIn(link.relative_to(self.root).as_posix(), names)

    def test_collector_requires_valid_checkpoint_hash_for_selected_lossless_audio(self):
        sys.path.insert(0, str(SCRIPT.parent))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPT.parent)))
        import book_content as content
        mp3 = self.root / self.paths[2]
        mp3.with_suffix(".wav").write_bytes(b"lossless fixture")
        mp3.with_suffix(".qa.json").write_text('{"warnings":[]}')
        inventory = {"recordings": [{"sourcePath": self.paths[2], "sha256": hashlib.sha256(mp3.read_bytes()).hexdigest(),
                                    "selectedClips": []}]}
        for checkpoint in [{}, {"output_sha256": {}}, {"output_sha256": {"lossless_wav": None}},
                           {"output_sha256": {"lossless_wav": ""}}, {"output_sha256": {"lossless_wav": "bad-hash"}},
                           {"output_sha256": None}, {"output_sha256": {"lossless_wav": 42}}]:
            with self.subTest(checkpoint=checkpoint):
                mp3.with_suffix(".checkpoint.json").write_text(json.dumps(checkpoint))
                with patch.object(content, "load_project_registry", return_value={}), \
                     patch.object(content, "load_recording_inventory", return_value=inventory):
                    with self.assertRaisesRegex(backup.BackupError, "production.*hash|lossless.*hash"):
                        backup.collect_release_fileset(self.root, "lumen-backup-fixture")

if __name__ == "__main__":
    unittest.main()
