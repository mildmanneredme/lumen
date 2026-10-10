# Lumen private backup and recovery

The existing destination is [Lumen in Google Drive](https://drive.google.com/drive/folders/1h2Qlk0pdiwG-Nsbc9i6hWxsIwXG7rCO4), folder ID `1h2Qlk0pdiwG-Nsbc9i6hWxsIwXG7rCO4`. Connector metadata on 10 October 2026 showed owner-only access and one existing `LUMEN-Rob-Xie-Charon-v8.m4b` file (945,449,160 bytes). Preserve that file and the folder's sharing. The Nimblip `test-apps` folder is not a Lumen destination.

The installed Drive desktop mount is `/Users/robxie/Library/CloudStorage/GoogleDrive-robert.s.xie@gmail.com/My Drive/Lumen`. `/Users/robxie/Google Drive/My Drive/Lumen` resolves to it, but use the canonical path for the backup command. Its existing Charon M4B matches the connector's name and size. The current Drive connector exposes file upload and readback, but no storage-quota/about action or documented multi-gigabyte upload limit. Drive desktop sync is the bulk route; verify account storage headroom before copying. A local mount's free-space report describes the Mac, not available Drive storage.

## Exact fileset

`web/scripts/backup_release.py` prepares an explicit private plan without reading large audio bodies. It reconciles the canonical registry and current production lineage first. The approved scope includes:

- All 496 selected raw WAVs and their adjacent generation metadata.
- The existing 182 mastered MP3s and 182 lossless mastered WAVs, with QA and mastering checkpoints.
- Manuscript versions, current generation/chapter/delivery manifests, production scripts, and local audit/recheck evidence.
- Reader code, assets, tests, continuity references, approved casting records, roadmap and recovery documents, and private release/proof artifacts.
- The exact 496 selected ASR JSON caches referenced by the finalized timing evidence, with their recorded `cacheSha256` values bound as expected hashes. The generic collector skips cache directories: augment its paths/hash bindings and call `prepare_fileset` again to produce fresh schema-2 identities and totals. Verify all 496 are present, exclude unrelated caches and downloaded model weights, and record the finalized evidence hashes used for selection. A generic `--plan` output alone cannot reproduce the finalizer after recovery.

Receipts and attempt/API response folders, environments, downloaded model caches, credentials, `.env` files, private keys, and redundant M4B/ZIP packages are excluded. Existing Drive packages are preserved. A newly selected audio take changes the fileset and requires a fresh plan. No 128 kbps alternate encode is part of this scope.

The initial read-only inventory was approximately 23.36 decimal GB (21.75 GiB), including 22.60 GB of selected raw audio, MP3s and lossless masters. The final plan records exact paths/counts/bytes. At least **3 GiB of free local space must remain** beyond the whole plan size before copying, and before each remaining file and flushed copy chunk. Account cloud storage must independently accommodate the backup. Do not duplicate the files in an intermediate staging archive.

## Plan, review, and copy

Finish source changes first. Use a fresh versioned ID for each snapshot; a destination that already exists is never overwritten. Keep the plan under the ignored author-audit folder. Planning alone creates no Drive files.

```sh
python3 web/scripts/backup_release.py \
  --plan Audiobook/author-audit/backup-plan-20261010-v1.json \
  --backup-id lumen-backup-20261010-v1
```

Review the explicit file list, production hash bindings, exclusions, byte total, available disk space, exact cloud destination and remaining account quota. After review, copy directly into the verified Lumen desktop sync mount:

```sh
python3 web/scripts/backup_release.py \
  --copy Audiobook/author-audit/backup-plan-20261010-v1.json \
  --destination '/Users/robxie/Library/CloudStorage/GoogleDrive-robert.s.xie@gmail.com/My Drive/Lumen/lumen-backup-20261010-v1'
```

The script streams each source once into a newly created file, calculates its SHA-256 during the copy, and compares audio with checkpoint hashes. Execution requires a fresh schema-2 plan, binding each source's device, inode, ctime, size, and mtime at planning and checking its opened descriptor before, during and after reading. Same-size replacements with restored mtime are rejected; a changed source leaves the backup incomplete. Schema-1 plans remain inspectable but must be regenerated before execution. Existing hash-bound backup manifests remain verifiable and restorable. It rejects source/destination symlinks, path escapes, credential paths, and public `web/dist` destinations. Directory descriptors with `O_NOFOLLOW` pin every source and destination ancestor during copying, hashing and metadata writes, so a symlink introduced after planning cannot redirect private bytes. Nonblocking opens reject substituted FIFOs without hanging. Pass the canonical resolved sync mount when Drive uses a mount alias. It does not alter source files, Drive permissions, or earlier backups.

`backup-state.json` records incomplete or prepared progress; it never claims completion. The exclusively created immutable `backup-manifest.json` is the sole completion marker and final write after all state updates. It appears only after every file passes and records exact paths, sizes, hashes, fileset digest and manifest digest. Publication checks bind its exact expected bytes, original file and directory identities, and all copied outputs. A persistent failure to write diagnostic state cannot mask the original copy error. A late I/O error is reconciled only when the exact expected manifest bytes remain readable through the owned destination; the returned `completionPublication.durabilityVerified: false` distinguishes readable completion from unconfirmed crash durability. Existing or partial markers are preserved and cannot be accepted as a successful publication. Interruptions retain the incomplete destination as evidence; it is not a usable completed snapshot. There is no automatic overwrite/resume mode. Diagnose the interruption before selecting a fresh snapshot ID; retain partial copies until an author-approved cleanup.

Both the local state and final manifest keep `remoteSyncStatus: pending`. Copy completion and a matching local restore are not proof of cloud upload. Reports must distinguish these states.

## Remote verification and restore proof

Wait for Drive desktop to finish syncing, then read the versioned folder back through the authenticated connector. Verify its parent is the existing Lumen folder and that inherited owner-only access remains unchanged. Traverse the complete folder tree with paginated Drive searches where necessary; match every manifest path and file size, with no missing assets. Drive `list_folder` can be bounded, so one short listing does not establish full coverage.

Record remote file IDs and observed parents/sizes/checksums in a separate private verification report. Use provider checksums when actually returned; never treat an ETag, local placeholder, or absent checksum as remote SHA-256 evidence. The currently exposed metadata action did not return requested file checksums in discovery. Download representative remote files through authenticated raw-file fetches for a small cloud restore check if complete remote checksums are unavailable. Keep the manifest immutable and attach verification as separate evidence.

For local restore proof, select at least a manuscript, raw WAV, mastered MP3, lossless master WAV, and release/QA JSON across both voices. Restore into a new temporary directory, then compare the restored bytes with the manifest:

```sh
python3 web/scripts/backup_release.py \
  --root . \
  --restore-sample '/Users/robxie/Library/CloudStorage/GoogleDrive-robert.s.xie@gmail.com/My Drive/Lumen/lumen-backup-20261010-v1' \
  --destination /private/tmp/lumen-restore-20261010-v1 \
  --path Draft/v6/part1.md \
  --path Audiobook/v7/raw/chapter-001-001.wav \
  --path Audiobook/v8/mastered/chapter-001.mp3 \
  --path Audiobook/v7/mastered/chapter-001.wav \
  --path Audiobook/v8/mastered/chapter-001.qa.json
```

Run this command from the project directory. Restoration requires an explicit `--root` and refuses a destination in that project's public `web/dist` tree. It reserves 3 GiB by default; `--minimum-free-bytes` is honored and validated. Selected bytes plus that reserve must fit before creating the destination, and remaining bytes plus the reserve are checked before each file and write chunk. A capacity failure leaves a partial sample without a successful restore report. Use paths from the final manifest; retake IDs can differ. This command binds its restore selection to the same signed manifest verified before copying and rejects altered manifest digests, unknown paths, symlinks and same-size byte tampering before claiming success. Its result says `verificationScope: local-restored-sample` and `remoteSyncVerified: false`. A full `--verify <snapshot>` streams every backed-up file and is an explicit additional read; do not run it as an incidental health check.

For disaster recovery, fetch the complete cloud snapshot into a fresh directory, validate the manifest and every file hash, restore the project layout under `files/`, and rerun canonical registry/inventory checks. Preserve original manuscript versions, production IDs/hashes and author review records. Supply credentials separately through the normal local environment; they are intentionally absent from the backup. Validate a local reader/audit session before deploying, and restore the matching app/release pointer together without clearing reader bookmarks.

## Validation and current status

```sh
python3 -m unittest discover -s web/tests -p test_backup_release.py -v
```

The 73 backup fixtures use tiny temporary files and cover exact copying, production hash mismatch, source changes and same-size replacements after planning, nonblocking FIFO rejection, OAuth and parent-directory credential/receipt and generated-directory exclusions, symlink rejection, snapshot and restore disk-space reserves, earlier-backup preservation, immutable manifests, and verified sample restore/tamper failure. Manifest descriptors remain bound throughout verification and restore, and success checks recheck earlier files and the logical restored destination. Flush, fsync, close, short-write and directory-sync fixtures exercise publication diagnostics and output identities without extra production hash reads. They do not upload files or read production audio bodies. A further 44 fixtures verify the archive route described in [archive-backup.md](archive-backup.md).

Destination discovery and engineering preparation are complete. The actual versioned copy, cloud-sync confirmation, remote inventory/checksum evidence, and production restore sample remain pending until their recorded checks have finished.
