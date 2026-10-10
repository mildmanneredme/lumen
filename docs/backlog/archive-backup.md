# Compressed private backup fallback

`web/scripts/archive_backup.py` streams the reviewed fileset from
`backup_release.py` directly into one versioned ZIP64 file. Deflate level 3
compresses stored bytes; restoring a WAV or MP3 returns the exact original
bytes. It does not transcode, master, regenerate, or alter either audiobook.
There is no intermediate copy of the audio and no temporary second archive.

The destination remains the existing private **Lumen** Google Drive folder:
`1h2Qlk0pdiwG-Nsbc9i6hWxsIwXG7rCO4`. Use its canonical Drive Desktop path:

```text
/Users/robxie/Library/CloudStorage/GoogleDrive-robert.s.xie@gmail.com/My Drive/Lumen
```

No archive has been copied there by this implementation. The existing Charon
M4B and all earlier backups stay in place. No sharing permissions change.
Finish the release commits, timing maps, and approval proofs before collecting
the final plan, because changing any selected source invalidates the snapshot.

## Space evidence and limits

The read-only check on 10 October 2026 selected 3,331 files totaling
23,363,803,259 bytes. It read 103,637,986 bytes across 12 selected raw/mastered
WAVs, sampling their beginning, middle, and end. The estimate uses the worst
observed compressed window in each WAV family plus ten percentage points;
all other files and unrepresented families assume no compression savings.
It also allows 1 KiB per file plus 9 MiB for ZIP structure and the manifest.

That conservative estimate was **23,071,602,653 bytes** (about 21.49 GiB).
Mastered WAV windows compressed poorly, so compression does not reliably
solve a low-space condition. The canonical Drive volume subsequently reported
30,269,440,000 free bytes, but available space changes while other work runs.
Read both the selected plan and the exact destination volume again immediately
before creation. The estimate is advisory; it never promises the archive fits.

The operator must supply a hard archive-size cap that fits alongside at least
**3 GiB** of free space. The writer checks that reserve on every source chunk
and every compressed write, including ZIP headers and final central-directory
records. It stops before writing beyond the cap. If the cap is reached, disk
space falls, or a source changes, the incomplete archive remains for inspection
and there is **no completion checksum sidecar**. Do not upload or call it a
backup. Retrying uses a new versioned backup ID; the script never overwrites
the failed file or any older backup.

## Local workflow

Create a private reviewed plan using `backup_release.py --plan`. It already
selects the current accepted MP3s, selected raw WAVs and their metadata,
lossless masters, manuscript, code, continuity references, and review evidence.
It excludes credentials, all `.env` variants, private keys, downloaded model weights,
environments, API receipts, unselected raw takes, and redundant M4B/ZIP builds.

The generic collector also skips cache directories. Before reviewing the final
execution plan, add only the exact ASR JSON paths referenced by the finalized
timing evidence, with their recorded `cacheSha256` values as expected hashes.
Use `prepare_fileset` to rebuild the augmented plan with fresh source identities
and totals. Verify all 496 selected caches are included, with no unreferenced
caches or model weights, and record the finalized evidence hashes used for
selection. These small files are needed to reproduce timing validation after
recovery; the generic `--plan` output alone is insufficient for this release.

Execution requires a fresh schema-2 plan. Each source is bound to its device,
inode, ctime, size, and mtime when planned, then checked through its opened
descriptor before and throughout reading. A replacement with the same size
and mtime is rejected. Older schema-1 plans can be inspected but must be
regenerated before archive creation; completed hash-bound backups remain
verifiable and restorable.

Run `archive_backup.py --estimate PLAN --root PROJECT --path SELECTED_WAV`
with up to 24 unique sample paths that appear in the plan. Include both raw
and mastered files from both editions. It reads bounded windows and writes
nothing. Each ZIP source keeps exactly its reviewed relative path, without
invented directory names or renaming audio files. `backup-manifest.json` is
reserved for the manifest inside the archive.

After review, create the archive with these explicit arguments:

```text
python3 web/scripts/archive_backup.py \
  --root /Users/robxie/Desktop/Novel-proj \
  --archive /PRIVATE/PATH/reviewed-backup-plan.json \
  --destination "/CANONICAL/DRIVE/Lumen/REVIEWED-BACKUP-ID.zip" \
  --maximum-archive-bytes REVIEWED_CAP_IN_BYTES
```

The filename must be the plan's exact `backupId` followed by `.zip`. Archive
and sidecar creation are exclusive, with local mode `0600`; newly restored
directories use `0700`. All ancestors and leaf files are opened with
descriptor traversal and `O_NOFOLLOW`. Sources must remain regular files
with the same planned device, inode, ctime, size, and mtime throughout the
operation. Nonblocking opens reject a substituted FIFO without hanging. Hashes
from production checkpoints are checked while compressing; every copied
source also receives a SHA-256 hash in the internal manifest.

A successful ZIP has a sibling `REVIEWED-BACKUP-ID.zip.checksums.json` with
its byte count, SHA-256, MD5, source byte count, plan hash, and manifest hash.
Both records explicitly say that remote sync is pending. If flush, fsync or close reports an error after the exclusive sidecar write, the writer accepts completion only after reading back the exact expected bytes from that same regular file and original directory. Its result then includes `completionPublication.durabilityVerified: false`; current readable bytes do not establish crash durability. Failed creation, partial writes, substituted files and different JSON remain failures. Normal and error publication both recheck the logical archive and marker paths against their original directory and full file identities. Verify all original entries locally, then restore a small explicit sample outside `web/dist`:

```text
python3 web/scripts/archive_backup.py --verify /PRIVATE/PATH/BACKUP.zip

python3 web/scripts/archive_backup.py \
  --root /Users/robxie/Desktop/Novel-proj \
  --restore-sample /PRIVATE/PATH/BACKUP.zip \
  --destination /PRIVATE/PATH/new-restore-directory \
  --path Draft/v6/part1.md \
  --path EXACT_SELECTED_AUDIO_PATH_FROM_MANIFEST
```

Use actual selected paths from the manifest, including a manuscript and a
selected audio file. The verifier first checks the full compressed archive
against its sidecar, then hashes every decompressed entry. It rejects missing,
extra, duplicated, traversal, encrypted, or symlink entries. Sample restoration
checks the archive's full compressed checksum, verifies the requested source
entries, writes only those entries into a new private directory, and hashes
the restored bytes again. Existing destinations are never overwritten.

## Proving the Drive upload

A file appearing in the local Drive Desktop mount is insufficient evidence.
After the sync client finishes, find the exact versioned filename within the
verified Lumen folder using the connected Google Drive tools, then request
file metadata. Check `parents` against the folder ID, `size` against
`archiveBytes`, and `sha256Checksum` against `archiveSha256` when available.
Also compare `md5Checksum` against `archiveMd5`. Google's
[Drive Files resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
documents these binary-file checksums and size fields.

Verify the checksum sidecar separately and confirm inherited sharing still
matches the owner-only Lumen folder. Record the returned file IDs, hashes,
sizes, parent folder ID, and verification time in a private release receipt.
Only then record remote sync as verified. If the connector does not expose a
server checksum, preserve a pending verification status until a supported
remote checksum or downloaded-byte check succeeds; local restore evidence
does not establish cloud durability.

## Validation

```text
python3 -m unittest discover -s web/tests -p test_archive_backup.py
```

Thirty-three archive fixtures and 52 backup fixtures verify original bytes, ZIP64 streaming, deterministic paths
and timestamps, checksum binding, full verification, sample restore, exclusive
files, public-path and symlink defenses, source identity changes, reserve and
size-cap failures, malformed entries, source replacement after planning,
nonblocking FIFO rejection, OAuth and parent-directory credential and generated-directory exclusions,
and explicit CLI restoration scope.
