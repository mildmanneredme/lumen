#!/usr/bin/env python3
"""Plan and copy an immutable private backup; cloud sync needs separate proof.

The script never contacts Drive or changes sharing. A reviewed plan is copied
only to an explicitly supplied destination. Source hashes are calculated while
copying, with production hashes checked when available. Restore verification
is local evidence; it never claims that Drive has finished uploading.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
import uuid


DEFAULT_RESERVE_BYTES = 3 * 1024 ** 3
CHUNK_BYTES = 1024 * 1024
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
BLOCKED_PARTS = {".git", ".vercel", ".aws", ".ssh", ".codex", ".agents", "receipts", "attempts",
                 "node_modules", "__pycache__", ".venv", "venv", "models-cache", "downloaded-models"}
BLOCKED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".pyc", ".lock"}
BLOCKED_FILES = {".netrc", ".npmrc", ".pypirc", ".git-credentials", ".ds_store",
                 "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"}
GENERATED_DIRECTORIES = {"env", "venv", "models", "cache", "caches", "checkpoints",
                         "downloaded-models", "models-cache", "pytest-cache", "mypy-cache",
                         "ruff-cache", "tox", "nox"}


class BackupError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise BackupError(message)


def encoded(value):
    try:
        return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                           allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise BackupError(f"Invalid backup JSON: {exc}") from exc


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def signed(value, field):
    result = dict(value)
    result[field] = digest({key: item for key, item in value.items() if key != field})
    return result


def read_json(path):
    try:
        with opened_parent(path) as (parent, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as incoming:
                before = os.fstat(incoming.fileno())
                require(stat.S_ISREG(before.st_mode), "Backup JSON source must be a regular file")
                raw = incoming.read(before.st_size + 1)
                require(len(raw) == before.st_size, "Backup JSON source byte count changed while reading")
                require_source_state(os.fstat(incoming.fileno()), source_state(before))
                value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"Cannot read backup JSON: {exc}") from exc
    require(isinstance(value, dict), "Backup JSON must be an object")
    return value


def safe_relative(name):
    require(isinstance(name, str) and name and "\x00" not in name and "\\" not in name,
            "Invalid backup relative path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(part not in ("", ".", "..") for part in name.split("/")),
            "Backup paths must be canonical relative paths without traversal")
    lower_parts = [part.lower() for part in path.parts]
    filename = lower_parts[-1]
    require(not any(part in BLOCKED_PARTS for part in lower_parts)
            and not any(part.startswith(".env") for part in lower_parts)
            and filename not in BLOCKED_FILES
            and Path(filename).suffix not in BLOCKED_SUFFIXES
            and not re.search(r"(?:^|[._-])(?:credentials?|private[-_]key|service[-_]account|api[-_]key|access[-_]token)(?:[._-]|$)", filename)
            and not re.search(r"(?:^|[._-])(?:oauth|client[-_]secrets?|refresh[-_]token|id[-_]token|auth[-_]token|tokens?)(?:[._-]|$)", filename),
            f"Excluded credential, receipt, environment, or key path: {name}")
    return path


def owned_file(root, name):
    relative = safe_relative(name)
    path = Path(root)
    for part in relative.parts:
        path /= part
        require(not path.is_symlink(), f"Backup source/file symlink is forbidden: {name}")
    require(path.is_file() and Path(root) in path.resolve().parents, f"Missing or escaped backup file: {name}")
    return path


def source_state(info):
    return {"bytes": info.st_size, "mtimeNs": info.st_mtime_ns,
            "sourceIdentity": {"device": info.st_dev, "inode": info.st_ino, "ctimeNs": info.st_ctime_ns}}


def source_clock(path):
    with opened_parent(path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode), "Backup source must be a regular file")
            return source_state(info)
        finally:
            os.close(descriptor)


def require_source_state(info, row):
    require(stat.S_ISREG(info.st_mode) and source_state(info) ==
            {key: row.get(key) for key in ("bytes", "mtimeNs", "sourceIdentity")},
            f"Backup source identity changed after planning: {row.get('path', 'opened source')}")


def validate_fileset(plan, *, require_source_identity=False):
    require(isinstance(plan, dict) and type(plan.get("schemaVersion")) is int and plan["schemaVersion"] in (1, 2),
            "Unsupported backup plan schema")
    require(not require_source_identity or plan["schemaVersion"] == 2,
            "Historical backup plans lack reviewed source identity; create a fresh plan before copying")
    require(isinstance(plan.get("backupId"), str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,120}", plan["backupId"]),
            "Invalid backup ID")
    require(plan.get("planSha256") == digest({key: value for key, value in plan.items() if key != "planSha256"}),
            "Backup plan digest changed")
    rows = plan.get("files")
    require(isinstance(rows, list) and bool(rows), "Backup plan needs files")
    seen = set()
    for row in rows:
        require(isinstance(row, dict), "Invalid backup file row")
        safe_relative(row.get("path"))
        require(row["path"] not in seen, "Duplicate backup source path")
        seen.add(row["path"])
        require(type(row.get("bytes")) is int and row["bytes"] >= 0 and type(row.get("mtimeNs")) is int,
                "Invalid backup source clock")
        if plan["schemaVersion"] == 2:
            identity = row.get("sourceIdentity")
            require(isinstance(identity, dict) and set(identity) == {"device", "inode", "ctimeNs"}
                    and all(type(identity[key]) is int for key in identity)
                    and identity["device"] >= 0 and identity["inode"] >= 0,
                    "Invalid reviewed backup source identity")
        if row.get("expectedSha256") is not None:
            require(isinstance(row["expectedSha256"], str) and SHA256.fullmatch(row["expectedSha256"]), "Invalid expected backup hash")
    require(type(plan.get("totalBytes")) is int and plan["totalBytes"] == sum(row["bytes"] for row in rows),
            "Backup byte total differs")
    return rows


def prepare_fileset(root, paths, backup_id, *, expected_hashes=None):
    """Read names and stat clocks only; do not rehash production audio to plan."""
    root = Path(root).resolve()
    require(isinstance(paths, (list, tuple)) and bool(paths) and all(isinstance(name, str) for name in paths),
            "Explicit backup paths are required")
    require(len(set(paths)) == len(paths), "Duplicate backup source path")
    expected_hashes = expected_hashes or {}
    require(isinstance(expected_hashes, dict) and set(expected_hashes).issubset(paths), "Unexpected expected-hash source path")
    rows = []
    for name in sorted(paths):
        path = owned_file(root, name)
        row = {"path": name, **source_clock(path)}
        if name in expected_hashes:
            row["expectedSha256"] = expected_hashes[name]
        rows.append(row)
    plan = signed({"schemaVersion": 2, "backupId": backup_id, "copyStatus": "planned",
                   "cloudDestinationFolderId": "1h2Qlk0pdiwG-Nsbc9i6hWxsIwXG7rCO4",
                   "files": rows, "totalBytes": sum(row["bytes"] for row in rows)}, "planSha256")
    validate_fileset(plan)
    return plan


def existing_directory(path):
    """Require a real existing parent; the caller passes the canonical sync mount."""
    path = Path(path).absolute()
    for parent in [path, *path.parents]:
        require(not parent.is_symlink(), f"Backup destination parent symlink is forbidden: {parent}")
    require(path.is_dir(), "Backup destination parent must already exist")
    return path.resolve()


@contextmanager
def opened_parent(path, *, create=False):
    """Pin every ancestor before opening bytes or writing a snapshot file.

    Callers resolve the selected project/sync roots once. Owned descendants
    stay logical paths, so a later symlink cannot redirect a copy or restore.
    """
    path = Path(path).absolute()
    require(path.name and all(part not in (".", "..") and "\x00" not in part
                              for part in path.parts[1:]), "Unsafe backup file path")
    descriptors = []
    try:
        descriptors.append(os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for part in path.parts[1:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
            descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
        yield descriptors[-1], path.name
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def create_snapshot_directory(destination, *, files=False):
    with opened_parent(destination) as (parent, name):
        os.mkdir(name, 0o700, dir_fd=parent)
        if files:
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                os.mkdir("files", 0o700, dir_fd=descriptor)
            finally:
                os.close(descriptor)


def write_json(path, value):
    temporary = path.name + ".tmp-" + uuid.uuid4().hex
    with opened_parent(path) as (parent, name):
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def file_digest(path):
    result = hashlib.sha256()
    with opened_parent(path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode), "Backup hash source must be a regular file")
            opened_state, count = source_state(before), 0
            while count < before.st_size:
                block = stream.read(min(CHUNK_BYTES, before.st_size - count))
                require(bool(block), "Backup hash source byte count changed while reading")
                require_source_state(os.fstat(stream.fileno()), opened_state)
                result.update(block)
                count += len(block)
            require(not stream.read(1), "Backup hash source grew while reading")
            require_source_state(os.fstat(stream.fileno()), opened_state)
    return result.hexdigest()


def copy_hashed(source, destination, *, maximum_bytes=None, expected_source=None, before_write=None):
    """Copy at most the approved byte count; reject growth before writing it."""
    require(maximum_bytes is None or type(maximum_bytes) is int and maximum_bytes >= 0,
            "Invalid backup copy byte limit")
    result, count = hashlib.sha256(), 0
    with opened_parent(source) as (parent, name):
        source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(source_fd, "rb") as incoming:
            before = os.fstat(incoming.fileno())
            require(stat.S_ISREG(before.st_mode), "Backup source must be a regular file")
            if expected_source is not None:
                require_source_state(before, expected_source)
            opened_state = source_state(before)
            limit = before.st_size if maximum_bytes is None else maximum_bytes
            if before_write:
                before_write(0)
            with opened_parent(destination, create=True) as (outgoing_parent, outgoing_name):
                destination_fd = os.open(outgoing_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                         0o600, dir_fd=outgoing_parent)
                with os.fdopen(destination_fd, "wb") as outgoing:
                    while count < limit:
                        block = incoming.read(min(CHUNK_BYTES, limit - count))
                        require(bool(block), "Backup source byte count changed during copy")
                        require_source_state(os.fstat(incoming.fileno()), opened_state)
                        if before_write:
                            before_write(count)
                        outgoing.write(block); result.update(block); count += len(block)
                        if before_write:
                            # Account for buffered bytes before the next capacity check.
                            outgoing.flush()
                        require_source_state(os.fstat(incoming.fileno()), opened_state)
                    require(not incoming.read(1), "Backup source grew beyond its approved byte count")
                    require_source_state(os.fstat(incoming.fileno()), opened_state)
                    outgoing.flush(); os.fsync(outgoing.fileno())
                    if before_write:
                        before_write(count)
    return count, result.hexdigest()


def check_source_clock(root, row):
    path = owned_file(root, row["path"])
    require(source_clock(path) == {key: row.get(key) for key in ("bytes", "mtimeNs", "sourceIdentity")},
            f"Backup source identity changed after planning: {row['path']}")
    return path


def copy_snapshot(root, destination, plan, *, minimum_free_bytes=DEFAULT_RESERVE_BYTES, progress=None):
    root = Path(root).resolve()
    rows = validate_fileset(plan, require_source_identity=True)
    require(type(minimum_free_bytes) is int and minimum_free_bytes >= 0, "Invalid free-space reserve")
    destination = Path(destination).absolute()
    require(not destination.exists() and not destination.is_symlink(), "Backup destination already exists")
    parent = existing_directory(destination.parent)
    destination = parent / destination.name
    public_root = (root / "web/dist").resolve()
    require(destination != public_root and public_root not in destination.parents, "Backup destination cannot be public web/dist")
    require(destination.name == plan["backupId"], "Backup destination name must equal the reviewed backup ID")
    for row in rows:
        check_source_clock(root, row)
    require(shutil.disk_usage(parent).free >= plan["totalBytes"] + minimum_free_bytes,
            "Insufficient local free space for the backup plus reserve")
    create_snapshot_directory(destination, files=True)
    copied = []
    state = {"schemaVersion": 1, "backupId": plan["backupId"], "planSha256": plan["planSha256"],
             "copyStatus": "incomplete", "remoteSyncStatus": "pending", "files": copied,
             "totalBytes": plan["totalBytes"], "copiedBytes": 0}
    write_json(destination / "backup-state.json", state)
    try:
        for row in rows:
            require(shutil.disk_usage(parent).free >= row["bytes"] + minimum_free_bytes,
                    "Insufficient local free space during backup; incomplete copy retained")
            source = check_source_clock(root, row)
            count, source_hash = copy_hashed(source, destination / "files" / row["path"],
                                             maximum_bytes=row["bytes"], expected_source=row)
            require(count == row["bytes"], f"Backup source byte count changed: {row['path']}")
            check_source_clock(root, row)
            require(row.get("expectedSha256", source_hash) == source_hash, f"Backup production hash differs: {row['path']}")
            copied.append({"path": row["path"], "bytes": count, "sha256": source_hash})
            state["copiedBytes"] += count
            write_json(destination / "backup-state.json", state)
            if progress:
                progress(len(copied), len(rows), state["copiedBytes"], plan["totalBytes"])
        # Earlier files must remain unchanged while later files are copied.
        for row in rows:
            check_source_clock(root, row)
        manifest = signed({"schemaVersion": 1, "backupId": plan["backupId"], "planSha256": plan["planSha256"],
                           "createdAt": datetime.now(timezone.utc).isoformat(),
                           "cloudDestinationFolderId": plan["cloudDestinationFolderId"],
                           "copyStatus": "complete", "remoteSyncStatus": "pending",
                           "files": copied, "totalBytes": plan["totalBytes"]}, "manifestSha256")
        write_json(destination / "backup-manifest.json", manifest)
        state["copyStatus"] = "complete"
        state["manifestSha256"] = manifest["manifestSha256"]
        write_json(destination / "backup-state.json", state)
        return manifest
    except (OSError, BackupError) as exc:
        state["error"] = str(exc)
        write_json(destination / "backup-state.json", state)
        raise BackupError(f"Backup remains incomplete: {exc}") from exc


def manifest_files(snapshot, paths=None):
    snapshot = existing_directory(snapshot)
    manifest = read_json(owned_file(snapshot, "backup-manifest.json"))
    require(manifest.get("schemaVersion") == 1 and manifest.get("copyStatus") == "complete", "Backup is incomplete")
    require(manifest.get("manifestSha256") == digest({key: value for key, value in manifest.items() if key != "manifestSha256"}),
            "Backup manifest digest changed")
    rows = manifest.get("files")
    require(isinstance(rows, list) and bool(rows) and all(isinstance(row, dict) for row in rows), "Invalid backup manifest files")
    index = {}
    for row in rows:
        safe_relative(row.get("path"))
        require(row["path"] not in index and type(row.get("bytes")) is int and row["bytes"] >= 0
                and isinstance(row.get("sha256"), str) and SHA256.fullmatch(row["sha256"]), "Invalid backup manifest file/hash")
        index[row["path"]] = row
    require(manifest.get("totalBytes") == sum(row["bytes"] for row in rows), "Backup manifest byte total differs")
    if paths is not None:
        require(isinstance(paths, (list, tuple)) and bool(paths) and len(set(paths)) == len(paths), "Restore needs unique explicit paths")
        require(all(path in index for path in paths), "Unknown backup restore path")
        rows = [index[path] for path in paths]
    return snapshot, manifest, rows


def verify_snapshot(snapshot, paths=None):
    snapshot, manifest, rows = manifest_files(snapshot, paths)
    for row in rows:
        path = owned_file(snapshot, "files/" + row["path"])
        require(path.stat().st_size == row["bytes"] and file_digest(path) == row["sha256"],
                f"Backup current-byte hash/size differs: {row['path']}")
    return {"schemaVersion": 1, "backupId": manifest["backupId"], "manifestSha256": manifest["manifestSha256"],
            "verificationScope": "local-backup-all-files" if paths is None else "local-backup-sample",
            "verifiedFiles": len(rows), "verifiedBytes": sum(row["bytes"] for row in rows),
            "remoteSyncVerified": False, "verifiedAt": datetime.now(timezone.utc).isoformat()}


def restore_sample(root, snapshot, destination, paths, *, minimum_free_bytes=DEFAULT_RESERVE_BYTES):
    require(type(minimum_free_bytes) is int and minimum_free_bytes >= 0, "Invalid free-space reserve")
    root = Path(root).resolve()
    destination = Path(destination).absolute()
    require(not destination.exists() and not destination.is_symlink(), "Restore destination already exists")
    parent = existing_directory(destination.parent)
    destination = parent / destination.name
    public_root = (root / "web/dist").resolve()
    require(destination != public_root and public_root not in destination.parents,
            "Restore destination cannot be public web/dist")
    report = verify_snapshot(snapshot, paths)
    snapshot, _, rows = manifest_files(snapshot, paths)
    remaining = sum(row["bytes"] for row in rows)
    require(shutil.disk_usage(parent).free >= remaining + minimum_free_bytes,
            "Insufficient local free space for restored sample plus reserve")
    create_snapshot_directory(destination)
    def check_capacity(copied=0):
        require(shutil.disk_usage(parent).free >= remaining - copied + minimum_free_bytes,
                "Insufficient local free space during restore; incomplete sample retained")
    try:
        for row in rows:
            check_capacity()
            source = owned_file(snapshot, "files/" + row["path"])
            count, source_hash = copy_hashed(source, destination / row["path"], maximum_bytes=row["bytes"],
                                             before_write=check_capacity)
            require(count == row["bytes"] and source_hash == row["sha256"], f"Restore source changed during copy: {row['path']}")
            restored = owned_file(destination, row["path"])
            require(file_digest(restored) == row["sha256"], f"Restored byte hash differs: {row['path']}")
            remaining -= count
    except (OSError, BackupError) as exc:
        raise BackupError(f"Restore remains incomplete: {exc}") from exc
    return dict(report, verificationScope="local-restored-sample")


def collect_release_fileset(root, backup_id):
    """Select rebuild sources, current mastered/raw audio, code and review proof.

    Bulk redundant M4B/ZIP packages, API receipts, local environments, model
    downloads, and credential files are deliberately excluded. Checkpoint hashes
    bind large audio without reading its bytes until the actual copy.
    """
    import book_content as content
    root = Path(root).resolve()
    registry = content.load_project_registry(root)
    inventory = content.load_recording_inventory(root, registry)
    names, hashes = set(), {}

    def add(name, expected=None):
        owned_file(root, name)
        names.add(name)
        if expected is not None:
            require(isinstance(expected, str) and SHA256.fullmatch(expected), f"Missing production hash: {name}")
            require(name not in hashes or hashes[name] == expected, f"Conflicting production hash: {name}")
            hashes[name] = expected

    for recording in inventory["recordings"]:
        name = recording["sourcePath"]
        add(name, recording["sha256"])
        checkpoint_name = str(Path(name).with_suffix(".checkpoint.json"))
        qa_name = str(Path(name).with_suffix(".qa.json"))
        checkpoint = read_json(owned_file(root, checkpoint_name))
        add(checkpoint_name); add(qa_name)
        add(str(Path(name).with_suffix(".wav")), checkpoint.get("output_sha256", {}).get("lossless_wav"))
        for clip in recording["selectedClips"]:
            add(clip["sourcePath"], clip["rawAudioSha256"])
            add(str(Path(clip["sourcePath"]).with_suffix(".json")))
    roots = ["Draft", "docs", "web", "Audiobook/author-audit", "Audiobook/openai-review",
             "Audiobook/v7/local-checks", "Audiobook/v8/local-checks", "Audiobook/v8/qa-review"]
    excluded_backup_prefix = "Audiobook/author-audit/backup"
    for relative in roots:
        directory = root / relative
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            # Inspect directories directly below every selected root too;
            # approved character models are intentional source references.
            if (not path.is_relative_to(root / "web/art-direction") and
                    any(part.lower().lstrip(".").replace("_", "-") in GENERATED_DIRECTORIES
                        for part in path.relative_to(directory).parts[:-1])):
                continue
            if not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            if name.startswith(excluded_backup_prefix) or path.suffix.lower() in {".m4b", ".zip"}:
                continue
            try:
                safe_relative(name)
            except BackupError:
                continue
            add(name)
    for edition in ["v7", "v8"]:
        job = root / "Audiobook" / edition
        for path in list(job.glob("*.json")) + list(job.glob("*.py")) + list((job / "delivery").glob("*.json")) + list((job / "mastered").glob("*.json")):
            add(path.relative_to(root).as_posix())
    for path in root.iterdir():
        if path.is_file() and (path.suffix.lower() in {".md", ".json", ".py", ".txt", ".toml"} or path.name == ".gitignore"):
            try:
                safe_relative(path.name)
            except BackupError:
                continue
            add(path.name)
    return prepare_fileset(root, sorted(names), backup_id, expected_hashes=hashes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Selected project root; required for restoration")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", type=Path, help="Create a private fileset plan; no cloud writes")
    modes.add_argument("--copy", type=Path, help="Copy a reviewed fileset plan")
    modes.add_argument("--verify", type=Path, help="Hash an existing backup, locally")
    modes.add_argument("--restore-sample", type=Path, help="Verify and restore an explicit sample from this backup")
    parser.add_argument("--backup-id")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--path", action="append", default=[], help="Explicit sample path, repeatable")
    parser.add_argument("--minimum-free-bytes", type=int, default=DEFAULT_RESERVE_BYTES)
    args = parser.parse_args(argv)
    root = args.root or Path(__file__).resolve().parents[2]
    try:
        if args.plan:
            require(args.backup_id and not args.plan.exists(), "New plan path and --backup-id are required")
            public_root = (root.resolve() / "web/dist").resolve()
            plan_path = args.plan.resolve()
            require(plan_path != public_root and public_root not in plan_path.parents,
                    "Backup plan must remain private outside web/dist")
            plan = collect_release_fileset(root, args.backup_id)
            with opened_parent(args.plan, create=True) as (parent, name):
                descriptor = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded(plan)); stream.flush(); os.fsync(stream.fileno())
            print(json.dumps({"plan": str(args.plan.resolve()), "files": len(plan["files"]), "bytes": plan["totalBytes"],
                              "planSha256": plan["planSha256"], "minimumFreeBytes": plan["totalBytes"] + args.minimum_free_bytes}))
        elif args.copy:
            require(args.destination is not None, "--copy requires --destination")
            last = [0.0]
            def progress(done, total, copied, all_bytes):
                now = time.monotonic()
                if now - last[0] >= 10 or done == total:
                    print(json.dumps({"copiedFiles": done, "files": total, "copiedBytes": copied, "bytes": all_bytes}), flush=True)
                    last[0] = now
            manifest = copy_snapshot(root, args.destination, read_json(args.copy), minimum_free_bytes=args.minimum_free_bytes, progress=progress)
            print(json.dumps({"copyStatus": manifest["copyStatus"], "remoteSyncStatus": "pending", "files": len(manifest["files"]),
                              "bytes": manifest["totalBytes"], "manifestSha256": manifest["manifestSha256"]}))
        elif args.verify:
            print(json.dumps(verify_snapshot(args.verify, args.path or None)))
        else:
            require(args.root is not None, "--restore-sample requires explicit --root for the public boundary")
            require(args.destination is not None and args.path, "--restore-sample requires --destination and explicit --path values")
            print(json.dumps(restore_sample(args.root, args.restore_sample, args.destination, args.path,
                                            minimum_free_bytes=args.minimum_free_bytes)))
    except (BackupError, OSError, ValueError) as exc:
        parser.exit(2, f"Backup failed: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
