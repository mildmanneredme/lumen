#!/usr/bin/env python3
"""Stream a reviewed private fileset into an exclusive ZIP64 backup.

This is an alternative to a second uncompressed copy, not an audio conversion.
No network requests or Drive sharing changes occur. A local checksum sidecar
marks completion; remote upload must be verified separately.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import zipfile
import zlib

import backup_release as backup


CHUNK_BYTES = backup.CHUNK_BYTES
MANIFEST_NAME = "backup-manifest.json"
MANIFEST_LIMIT = 8 * 1024 * 1024
MD5 = re.compile(r"[a-f0-9]{32}\Z")
LUMEN_FOLDER_ID = "1h2Qlk0pdiwG-Nsbc9i6hWxsIwXG7rCO4"


def sidecar_path(path):
    return Path(str(path) + ".checksums.json")


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def open_source(root, row):
    """Pin ancestors and check the exact descriptor before and after reading."""
    path = backup.check_source_clock(root, row)
    with backup.opened_parent(path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as incoming:
            before = os.fstat(incoming.fileno())
            backup.require_source_state(before, row)
            yield incoming
            backup.require(identity(os.fstat(incoming.fileno())) == identity(before),
                           f"Backup source changed during read: {row['path']}")
    backup.check_source_clock(root, row)


def check_free(parent_descriptor, reserve, next_bytes=0):
    backup.require(backup.available_bytes(parent_descriptor) >= reserve + next_bytes,
                   "Insufficient local free space during archive; incomplete file retained")


class GuardedOutput:
    """Check the reserve and absolute file-size cap on every ZIP write."""
    def __init__(self, stream, parent_descriptor, reserve, maximum):
        self.stream, self.parent_descriptor, self.reserve, self.maximum = stream, parent_descriptor, reserve, maximum
        self.high_water = 0

    def write(self, data):
        end = self.stream.tell() + len(data)
        backup.require(max(self.high_water, end) <= self.maximum, "Compressed backup exceeded its explicit archive cap")
        check_free(self.parent_descriptor, self.reserve, max(0, end - self.high_water))
        written = self.stream.write(data)
        backup.require(written == len(data), "Incomplete compressed backup write")
        self.high_water = max(self.high_water, end)
        return written

    def tell(self):
        return self.stream.tell()

    def seek(self, *args):
        return self.stream.seek(*args)

    def flush(self):
        return self.stream.flush()


def private_destination(root, destination):
    root = backup.existing_directory(root)
    destination = Path(destination).absolute()
    backup.require(not destination.exists() and not destination.is_symlink(), "Backup destination already exists")
    parent = backup.existing_directory(destination.parent)
    destination = parent / destination.name
    public = (root / "web/dist").resolve()
    backup.require(destination != public and public not in destination.parents, "Backup destination cannot be public web/dist")
    return root, destination, parent


def info_for(name):
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info._compresslevel = 3
    return info


def require_owned_path(path, parent_identity, file_identity, label):
    """Bind a logical output path to its original directory and regular file."""
    with backup.opened_parent(path) as (parent, name):
        current_parent = os.fstat(parent)
        backup.require((current_parent.st_dev, current_parent.st_ino) == parent_identity,
                       f"{label} logical parent changed during publication")
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        backup.require(stat.S_ISREG(current.st_mode) and identity(current) == file_identity,
                       f"{label} logical file changed during publication")


def require_owned_directory(path, parent_identity, directory_identity):
    with backup.opened_parent(path) as (parent, name):
        parent_info = os.fstat(parent)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        backup.require((parent_info.st_dev, parent_info.st_ino) == parent_identity and
                       stat.S_ISDIR(current.st_mode) and
                       (current.st_dev, current.st_ino) == directory_identity,
                       "Restore destination directory changed during verification")


def readback_owned_json(path, payload, parent_identity, owned_file, expected_identity=None):
    """Perform one bounded exact-byte read, retaining owned logical bindings."""
    with backup.opened_parent(path) as (parent, name):
        parent_info = os.fstat(parent)
        backup.require((parent_info.st_dev, parent_info.st_ino) == parent_identity,
                       "Completion sidecar parent changed after write error")
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as incoming:
            before = os.fstat(incoming.fileno())
            backup.require(stat.S_ISREG(before.st_mode) and (before.st_dev, before.st_ino) == owned_file,
                           "Completion sidecar ownership changed after write error")
            backup.require(expected_identity is None or identity(before) == expected_identity,
                           "Completion sidecar identity changed before error readback")
            backup.require(before.st_size == len(payload) and incoming.read(len(payload) + 1) == payload,
                           "Completion sidecar bytes differ after write error")
            backup.require(identity(os.fstat(incoming.fileno())) == identity(before) ==
                           identity(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                           "Completion sidecar changed during error readback")
    # A pinned old directory can survive a logical-path replacement.
    require_owned_path(path, parent_identity, identity(before), "Completion sidecar")
    return identity(before)


def write_exclusive_json(path, value):
    """Publish exclusively; reconcile a late I/O error against owned bytes.

    Some filesystems report a flush/fsync/close error after the full marker is
    already readable. Its exact current bytes then mark a complete local copy,
    while durability remains unverified. Never recover a failed exclusive open,
    a replaced marker, a partial write, or merely parseable but different JSON.
    """
    payload = backup.encoded(value)
    owned_file = owned_parent = final_identity = None
    try:
        with backup.opened_parent(path) as (parent, name):
            parent_info = os.fstat(parent)
            owned_parent = parent_info.st_dev, parent_info.st_ino
            descriptor = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            created = os.fstat(descriptor)
            owned_file = created.st_dev, created.st_ino
            try:
                outgoing = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with outgoing:
                backup.require(outgoing.write(payload) == len(payload), "Incomplete completion sidecar write")
                outgoing.flush()
                final_identity = identity(os.fstat(outgoing.fileno()))
                os.fsync(outgoing.fileno())
            # The archive was already synced and shares this owned directory.
            # Sync its two new directory entries before claiming durability.
            os.fsync(parent)
        final_identity = readback_owned_json(path, payload, owned_parent, owned_file, final_identity)
    except OSError as error:
        if owned_file is None:
            raise
        # Reopen through checked ancestors after the writer has closed. The
        # recovery is bounded by the expected payload and pinned to this call's
        # exclusively created regular file, not any preexisting completion file.
        try:
            final_identity = readback_owned_json(path, payload, owned_parent, owned_file, final_identity)
        except (OSError, backup.BackupError) as failure:
            raise backup.BackupError(f"Completion sidecar write failed and exact owned-byte readback failed: {failure}") from error
        return {"parentIdentity": owned_parent, "fileIdentity": final_identity,
                "diagnostic": {"status": "exact-readback-after-write-error", "durabilityVerified": False, "error": str(error)}}
    return {"parentIdentity": owned_parent, "fileIdentity": final_identity, "diagnostic": None}


def hash_stream(stream):
    sha, md5, count = hashlib.sha256(), hashlib.md5(), 0
    stream.seek(0)
    for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
        sha.update(block); md5.update(block); count += len(block)
    return count, sha.hexdigest(), md5.hexdigest()


def source_states(root, rows):
    states = {}
    for row in rows:
        with open_source(root, row) as incoming:
            states[row["path"]] = identity(os.fstat(incoming.fileno()))
    return states


def assert_sources_unchanged(root, rows, states):
    for row in rows:
        with open_source(root, row) as incoming:
            backup.require(identity(os.fstat(incoming.fileno())) == states[row["path"]],
                           f"Backup source identity changed after planning: {row['path']}")


def archive_snapshot(root, destination, plan, *, maximum_archive_bytes,
                     minimum_free_bytes=backup.DEFAULT_RESERVE_BYTES, progress=None):
    """Write only the ZIP and sidecar. No intermediate copied audio exists."""
    rows = backup.validate_fileset(plan, require_source_identity=True)
    backup.require(plan.get("copyStatus") == "planned" and plan.get("cloudDestinationFolderId") == LUMEN_FOLDER_ID,
                   "Archive needs a planned fileset for the verified private Lumen folder")
    backup.require(MANIFEST_NAME not in {row["path"] for row in rows}, "Backup manifest path is reserved")
    backup.require(type(maximum_archive_bytes) is int and maximum_archive_bytes > 0, "An explicit positive archive cap is required")
    backup.require(type(minimum_free_bytes) is int and minimum_free_bytes >= 0, "Invalid free-space reserve")
    root, destination, parent = private_destination(root, destination)
    backup.require(destination.name == plan["backupId"] + ".zip", "Archive filename must equal the reviewed backup ID plus .zip")
    checksum_path = sidecar_path(destination)
    backup.require(not checksum_path.exists() and not checksum_path.is_symlink(), "Backup checksum sidecar already exists")
    states = source_states(root, rows)
    publication = None
    try:
        with backup.opened_parent(destination) as (output_parent, name):
            backup.require(backup.available_bytes(output_parent) >= maximum_archive_bytes + minimum_free_bytes,
                           "Insufficient local free space for the explicit archive cap plus reserve")
            output_parent_info = os.fstat(output_parent)
            output_parent_identity = output_parent_info.st_dev, output_parent_info.st_ino
            descriptor = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=output_parent)
            with os.fdopen(descriptor, "w+b", buffering=0) as outgoing:
                guarded = GuardedOutput(outgoing, output_parent, minimum_free_bytes, maximum_archive_bytes)
                files, copied_bytes = [], 0
                with zipfile.ZipFile(guarded, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3,
                                     allowZip64=True) as archive:
                    for row in sorted(rows, key=lambda item: item["path"]):
                        result, count = hashlib.sha256(), 0
                        with open_source(root, row) as incoming:
                            backup.require(identity(os.fstat(incoming.fileno())) == states[row["path"]],
                                           f"Backup source identity changed after planning: {row['path']}")
                            with archive.open(info_for(row["path"]), "w", force_zip64=True) as entry:
                                for block in iter(lambda: incoming.read(CHUNK_BYTES), b""):
                                    check_free(output_parent, minimum_free_bytes)
                                    backup.require_source_state(os.fstat(incoming.fileno()), row)
                                    entry.write(block); result.update(block); count += len(block)
                                    backup.require_source_state(os.fstat(incoming.fileno()), row)
                                    backup.require(count <= row["bytes"], f"Backup source grew: {row['path']}")
                        source_hash = result.hexdigest()
                        backup.require(count == row["bytes"], f"Backup source byte count changed: {row['path']}")
                        backup.require(row.get("expectedSha256", source_hash) == source_hash,
                                       f"Backup production hash differs: {row['path']}")
                        files.append({"path": row["path"], "bytes": count, "sha256": source_hash})
                        copied_bytes += count
                        if progress:
                            progress(len(files), len(rows), copied_bytes, plan["totalBytes"], guarded.high_water)
                    assert_sources_unchanged(root, rows, states)
                    manifest = backup.signed({"schemaVersion": 1, "backupId": plan["backupId"],
                        "planSha256": plan["planSha256"], "createdAt": datetime.now(timezone.utc).isoformat(),
                        "cloudDestinationFolderId": plan["cloudDestinationFolderId"], "copyStatus": "complete",
                        "remoteSyncStatus": "pending", "compression": "zip64-deflate-3",
                        "files": files, "totalBytes": plan["totalBytes"]}, "manifestSha256")
                    manifest_bytes = backup.encoded(manifest)
                    backup.require(len(manifest_bytes) <= MANIFEST_LIMIT, "Backup manifest is too large")
                    archive.writestr(info_for(MANIFEST_NAME), manifest_bytes)
                outgoing.flush()
                archive_identity = identity(os.fstat(outgoing.fileno()))
                os.fsync(outgoing.fileno())
                backup.require(identity(os.fstat(outgoing.fileno())) == archive_identity,
                               "Backup archive changed during file sync")
                archive_bytes, archive_hash, archive_md5 = hash_stream(outgoing)
                backup.require(identity(os.fstat(outgoing.fileno())) == archive_identity,
                               "Backup archive changed during checksum calculation")
                assert_sources_unchanged(root, rows, states)
            sidecar = {"schemaVersion": 1, "backupId": plan["backupId"], "planSha256": plan["planSha256"],
                       "manifestSha256": manifest["manifestSha256"], "archiveBytes": archive_bytes,
                       "archiveSha256": archive_hash, "archiveMd5": archive_md5,
                       "sourceBytes": plan["totalBytes"], "files": len(files), "copyStatus": "complete",
                       "remoteSyncStatus": "pending", "cloudDestinationFolderId": plan["cloudDestinationFolderId"]}
            check_free(output_parent, minimum_free_bytes, len(backup.encoded(sidecar)))
            require_owned_path(destination, output_parent_identity, archive_identity, "Backup archive")
            publication = write_exclusive_json(checksum_path, sidecar)
            backup.require(publication["parentIdentity"] == output_parent_identity,
                           "Completion sidecar parent differs from its archive")
            try:
                require_owned_path(destination, output_parent_identity, archive_identity, "Backup archive")
                require_owned_path(checksum_path, output_parent_identity, publication["fileIdentity"], "Completion sidecar")
            except OSError as error:
                # The marker has already been exclusively published. Reconcile one
                # transient final-check I/O error, never a changed identity or an
                # unbounded stream of retries; no source/audio hashes are repeated.
                try:
                    require_owned_path(destination, output_parent_identity, archive_identity, "Backup archive")
                    readback_owned_json(checksum_path, backup.encoded(sidecar), output_parent_identity,
                                        publication["fileIdentity"][:2], publication["fileIdentity"])
                    require_owned_path(destination, output_parent_identity, archive_identity, "Backup archive")
                except (OSError, backup.BackupError) as failure:
                    raise backup.BackupError(f"Published archive final check and exact owned-byte reconciliation failed: {failure}") from error
                diagnostic = {"status": "exact-readback-after-write-error", "durabilityVerified": False,
                              "error": str(error), "phase": "post-publication-identity-check"}
                if publication["diagnostic"]:
                    diagnostic["priorPublicationError"] = publication["diagnostic"]["error"]
                publication["diagnostic"] = diagnostic
            return dict(sidecar, completionPublication=publication["diagnostic"]) if publication["diagnostic"] else sidecar
    except (OSError, backup.BackupError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, OSError) and publication is not None:
            # The capacity descriptor stays open through publication. A late
            # close error must reconcile its known completed output, too.
            try:
                require_owned_path(destination, output_parent_identity, archive_identity, "Backup archive")
                readback_owned_json(checksum_path, backup.encoded(sidecar), output_parent_identity,
                                    publication["fileIdentity"][:2], publication["fileIdentity"])
                require_owned_path(destination, output_parent_identity, archive_identity, "Backup archive")
            except (OSError, backup.BackupError):
                pass
            else:
                diagnostic = {"status": "exact-readback-after-write-error", "durabilityVerified": False,
                              "error": str(exc), "phase": "output-parent-close"}
                if publication["diagnostic"]:
                    diagnostic["priorPublicationError"] = publication["diagnostic"]["error"]
                return dict(sidecar, completionPublication=diagnostic)
        raise backup.BackupError(f"Archive remains incomplete without a completion sidecar: {exc}") from exc


@contextmanager
def checked_small_json(path, maximum):
    """Keep bounded metadata and its original logical file pinned through use."""
    with backup.opened_parent(path) as (parent, name):
        parent_info = os.fstat(parent)
        parent_identity = parent_info.st_dev, parent_info.st_ino
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as incoming:
            before = os.fstat(incoming.fileno())
            backup.require(stat.S_ISREG(before.st_mode), "Backup metadata must be a regular file")
            backup.require(before.st_size <= maximum, "Backup metadata is too large")
            value = incoming.read(before.st_size + 1)
            backup.require(len(value) == before.st_size and identity(os.fstat(incoming.fileno())) == identity(before),
                           "Backup metadata changed during read")
            result = json.loads(value)
            backup.require(isinstance(result, dict), "Backup metadata must be an object")
            require_owned_path(path, parent_identity, identity(before), "Backup metadata")
            yield result
            backup.require(identity(os.fstat(incoming.fileno())) == identity(before),
                           "Backup metadata changed during verification")
    require_owned_path(path, parent_identity, identity(before), "Backup metadata")


def read_small_json(path, maximum):
    with checked_small_json(path, maximum) as value:
        return value


def validate_manifest(manifest, sidecar):
    backup.require(isinstance(manifest, dict) and type(manifest.get("schemaVersion")) is int and manifest["schemaVersion"] == 1
                   and manifest.get("copyStatus") == "complete", "Backup manifest is incomplete")
    backup.require(manifest.get("manifestSha256") == backup.digest({key: value for key, value in manifest.items()
                   if key != "manifestSha256"}) == sidecar["manifestSha256"], "Backup manifest hash differs")
    backup.require(manifest.get("backupId") == sidecar["backupId"] and
                   manifest.get("planSha256") == sidecar["planSha256"] and
                   manifest.get("cloudDestinationFolderId") == sidecar["cloudDestinationFolderId"] == LUMEN_FOLDER_ID,
                   "Backup manifest identity differs")
    rows = manifest.get("files")
    backup.require(isinstance(rows, list) and bool(rows), "Backup manifest needs files")
    index = {}
    for row in rows:
        backup.require(isinstance(row, dict), "Invalid backup manifest row")
        backup.safe_relative(row.get("path"))
        backup.require(row["path"] != MANIFEST_NAME and row["path"] not in index,
                       "Duplicate or reserved archive path")
        backup.require(type(row.get("bytes")) is int and row["bytes"] >= 0 and
                       isinstance(row.get("sha256"), str) and backup.SHA256.fullmatch(row["sha256"]),
                       "Invalid backup manifest byte count or hash")
        index[row["path"]] = row
    backup.require(manifest.get("totalBytes") == sum(row["bytes"] for row in rows) == sidecar["sourceBytes"]
                   and len(rows) == sidecar["files"], "Backup manifest byte/file total differs")
    return index


@contextmanager
def checked_archive(path):
    path = Path(path).absolute()
    backup.existing_directory(path.parent)
    try:
        with checked_small_json(sidecar_path(path), 64 * 1024) as sidecar:
            backup.require(type(sidecar.get("schemaVersion")) is int and sidecar["schemaVersion"] == 1
                           and sidecar.get("copyStatus") == "complete",
                           "Archive completion sidecar is missing or incomplete")
            backup.require(isinstance(sidecar.get("backupId"), str) and
                           re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,120}", sidecar["backupId"]), "Invalid archive backup ID")
            for key in ["archiveSha256", "manifestSha256", "planSha256"]:
                backup.require(isinstance(sidecar.get(key), str) and backup.SHA256.fullmatch(sidecar[key]), "Invalid archive hash")
            backup.require(isinstance(sidecar.get("archiveMd5"), str) and MD5.fullmatch(sidecar["archiveMd5"]), "Invalid archive MD5")
            backup.require(type(sidecar.get("archiveBytes")) is int and sidecar["archiveBytes"] > 0
                           and type(sidecar.get("sourceBytes")) is int and sidecar["sourceBytes"] >= 0
                           and type(sidecar.get("files")) is int and sidecar["files"] > 0, "Invalid archive size/count")
            with backup.opened_parent(path) as (parent, name):
                parent_info = os.fstat(parent)
                parent_identity = parent_info.st_dev, parent_info.st_ino
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(descriptor, "rb") as incoming:
                    before = os.fstat(incoming.fileno())
                    backup.require(stat.S_ISREG(before.st_mode), "Backup archive must be a regular file")
                    count, sha, md5 = hash_stream(incoming)
                    backup.require(count == sidecar["archiveBytes"] and sha == sidecar["archiveSha256"]
                                   and md5 == sidecar["archiveMd5"], "Backup archive current-byte hash/size differs")
                    incoming.seek(0)
                    with zipfile.ZipFile(incoming) as archive:
                        entries = archive.infolist()
                        names = [info.filename for info in entries]
                        backup.require(len(names) == len(set(names)) and MANIFEST_NAME in names,
                                       "Archive contains duplicate paths or lacks its manifest")
                        for info in entries:
                            backup.safe_relative(info.filename)
                            mode = info.external_attr >> 16
                            backup.require(not info.is_dir() and not stat.S_ISLNK(mode) and
                                           (stat.S_IFMT(mode) in (0, stat.S_IFREG)) and not info.flag_bits & 1,
                                           "Archive entries must be unencrypted regular files without symlinks")
                        manifest_info = archive.getinfo(MANIFEST_NAME)
                        backup.require(manifest_info.file_size <= MANIFEST_LIMIT, "Archive manifest is too large")
                        manifest = json.loads(archive.read(manifest_info))
                        index = validate_manifest(manifest, sidecar)
                        backup.require(set(names) == set(index) | {MANIFEST_NAME}, "Archive contains unexpected or missing files")
                        for name, row in index.items():
                            backup.require(archive.getinfo(name).file_size == row["bytes"], f"Archive entry byte count differs: {name}")
                        yield archive, manifest, index, sidecar
                    backup.require(identity(os.fstat(incoming.fileno())) == identity(before), "Backup archive changed during verification")
            require_owned_path(path, parent_identity, identity(before), "Backup archive")
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
        if isinstance(exc, backup.BackupError):
            raise
        raise backup.BackupError(f"Cannot verify private backup archive: {exc}") from exc


def selected_rows(index, paths):
    if paths is None:
        return list(index.values())
    backup.require(isinstance(paths, (list, tuple)) and bool(paths) and all(isinstance(name, str) for name in paths)
                   and len(set(paths)) == len(paths), "Restore needs unique explicit paths")
    backup.require(all(name in index for name in paths), "Unknown backup restore path")
    return [index[name] for name in paths]


def read_entry(archive, row, outgoing=None):
    sha, count = hashlib.sha256(), 0
    with archive.open(row["path"]) as incoming:
        for block in iter(lambda: incoming.read(CHUNK_BYTES), b""):
            count += len(block)
            backup.require(count <= row["bytes"], f"Archive entry exceeds manifest size: {row['path']}")
            if outgoing:
                outgoing.write(block)
            sha.update(block)
    backup.require(count == row["bytes"] and sha.hexdigest() == row["sha256"], f"Archive entry hash differs: {row['path']}")


def verification_report(manifest, rows, scope):
    return {"schemaVersion": 1, "backupId": manifest["backupId"], "manifestSha256": manifest["manifestSha256"],
            "verificationScope": scope, "verifiedFiles": len(rows), "verifiedBytes": sum(row["bytes"] for row in rows),
            "remoteSyncVerified": False, "verifiedAt": datetime.now(timezone.utc).isoformat()}


def verify_archive(path):
    with checked_archive(path) as (archive, manifest, index, _):
        rows = selected_rows(index, None)
        for row in rows:
            read_entry(archive, row)
        report = verification_report(manifest, rows, "local-archive-all-files")
    return report


def restore_sample(root, path, destination, paths, *, minimum_free_bytes=backup.DEFAULT_RESERVE_BYTES):
    root, destination, parent = private_destination(root, destination)
    backup.require(type(minimum_free_bytes) is int and minimum_free_bytes >= 0, "Invalid free-space reserve")
    with checked_archive(path) as (archive, manifest, index, _):
        rows = selected_rows(index, paths)
        for row in rows:
            read_entry(archive, row)
        with backup.opened_parent(destination) as (snapshot_parent, snapshot_name):
            backup.require(backup.available_bytes(snapshot_parent) >= sum(row["bytes"] for row in rows) + minimum_free_bytes,
                           "Insufficient local free space for restored sample plus reserve")
            backup.create_snapshot_directory(destination)
            snapshot_parent_info = os.fstat(snapshot_parent)
            snapshot_info = os.stat(snapshot_name, dir_fd=snapshot_parent, follow_symlinks=False)
            backup.require(stat.S_ISDIR(snapshot_info.st_mode), "Restore destination must be a real directory")
            snapshot_parent_identity = snapshot_parent_info.st_dev, snapshot_parent_info.st_ino
            snapshot_identity = snapshot_info.st_dev, snapshot_info.st_ino
            restored_files = []
            for row in rows:
                with backup.opened_parent(destination / row["path"], create=True) as (output_parent, name):
                    output_parent_info = os.fstat(output_parent)
                    output_parent_identity = output_parent_info.st_dev, output_parent_info.st_ino
                    descriptor = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=output_parent)
                    with os.fdopen(descriptor, "wb") as outgoing:
                        guarded = GuardedOutput(outgoing, output_parent, minimum_free_bytes, row["bytes"])
                        read_entry(archive, row, guarded)
                        outgoing.flush(); os.fsync(outgoing.fileno()); os.fsync(output_parent)
                        output_identity = identity(os.fstat(outgoing.fileno()))
                backup.require(backup.file_digest(destination / row["path"]) == row["sha256"], "Restored sample hash differs")
                restored_files.append((destination / row["path"], output_parent_identity, output_identity))
            require_owned_directory(destination, snapshot_parent_identity, snapshot_identity)
            for output, output_parent_identity, output_identity in restored_files:
                require_owned_path(output, output_parent_identity, output_identity, "Restored sample")
            report = verification_report(manifest, rows, "local-restored-archive-sample")
    return report


def compression_group(path):
    if Path(path).suffix.lower() != ".wav":
        return "other"
    return "raw-wav" if "/raw/" in path else "lossless-wav"


def estimate_archive(root, plan, sample_paths, *, window_bytes=4 * 1024 * 1024):
    """Bounded read-only samples; unrepresented groups use no savings."""
    root = backup.existing_directory(root)
    rows = backup.validate_fileset(plan, require_source_identity=True)
    index = {row["path"]: row for row in rows}
    selected = selected_rows(index, sample_paths)
    backup.require(len(selected) <= 24 and type(window_bytes) is int and 1 <= window_bytes <= 8 * 1024 * 1024,
                   "Compression estimation needs at most 24 samples and bounded windows")
    backup.require(all(Path(row["path"]).suffix.lower() == ".wav" for row in selected), "Compression samples must be selected WAV files")
    samples, sampled_bytes, maximum_ratios = [], 0, {}
    for row in selected:
        windows = []
        with open_source(root, row) as incoming:
            length = min(window_bytes, row["bytes"])
            for offset in sorted({0, max(0, (row["bytes"] - length) // 2), max(0, row["bytes"] - length)}):
                incoming.seek(offset)
                block = incoming.read(length)
                backup.require(len(block) == length, "Compression sample source changed")
                ratio = len(zlib.compress(block, 3)) / len(block) if block else 1.0015
                windows.append({"offset": offset, "bytes": len(block), "ratio": round(ratio, 6)})
                sampled_bytes += len(block)
                group = compression_group(row["path"])
                maximum_ratios[group] = max(maximum_ratios.get(group, 0), ratio)
        samples.append({"path": row["path"], "windows": windows})
    groups = {}
    for row in rows:
        group = compression_group(row["path"])
        groups.setdefault(group, {"sourceBytes": 0})["sourceBytes"] += row["bytes"]
    for group, values in groups.items():
        # Ten percentage points above the worst observed WAV window; never
        # claim savings for MP3, artwork, metadata, or unsampled WAV families.
        ratio = min(1.0015, maximum_ratios[group] + 0.10) if group in maximum_ratios else 1.0015
        values.update(ratio=round(ratio, 6), sampled=group in maximum_ratios,
                      estimatedBytes=math.ceil(values["sourceBytes"] * ratio))
    overhead = len(rows) * 1024 + MANIFEST_LIMIT + 1024 * 1024
    return {"schemaVersion": 1, "planSha256": plan["planSha256"], "sourceBytes": plan["totalBytes"],
            "estimatedArchiveBytes": sum(value["estimatedBytes"] for value in groups.values()) + overhead,
            "overheadBytes": overhead, "sampledBytes": sampled_bytes, "samples": samples, "groups": groups,
            "guaranteedToFit": False, "method": "worst-sampled-window-plus-ten-percentage-points; other files uncompressed"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--estimate", type=Path, help="Reviewed fileset plan; read-only bounded WAV samples")
    modes.add_argument("--archive", type=Path, help="Reviewed fileset plan; create one exclusive archive")
    modes.add_argument("--verify", type=Path, help="Verify all original-byte hashes in an archive")
    modes.add_argument("--restore-sample", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--maximum-archive-bytes", type=int)
    parser.add_argument("--minimum-free-bytes", type=int, default=backup.DEFAULT_RESERVE_BYTES)
    args = parser.parse_args(argv)
    try:
        if args.verify:
            result = verify_archive(args.verify)
        else:
            backup.require(args.root is not None, "An explicit --root is required")
            if args.estimate:
                result = estimate_archive(args.root, backup.read_json(args.estimate), args.path)
            elif args.archive:
                backup.require(args.destination is not None and args.maximum_archive_bytes is not None,
                               "Archive requires --destination and --maximum-archive-bytes")
                last = [0.0]
                def progress(done, total, copied, all_bytes, compressed):
                    now = time.monotonic()
                    if now - last[0] >= 10 or done == total:
                        print(json.dumps({"archivedFiles": done, "files": total, "sourceBytesRead": copied,
                                          "sourceBytes": all_bytes, "archiveBytesWritten": compressed}), flush=True)
                        last[0] = now
                result = archive_snapshot(args.root, args.destination, backup.read_json(args.archive),
                    maximum_archive_bytes=args.maximum_archive_bytes, minimum_free_bytes=args.minimum_free_bytes,
                    progress=progress)
            else:
                backup.require(args.destination is not None and args.path, "Restore requires --destination and explicit --path values")
                result = restore_sample(args.root, args.restore_sample, args.destination, args.path,
                                        minimum_free_bytes=args.minimum_free_bytes)
        print(json.dumps(result))
    except (backup.BackupError, OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Private archive backup failed: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
