# Canonical book data and release gates

Implemented foundation: `web/scripts/book_content.py`. This prepares private
release data while the narration audit is in progress. It preserves the current
pilot and never generates audio, copies one voice's timestamps to another, or
turns technical completion into human content approval.

Run the contract tests and reconcile the current production metadata:

```sh
python3 -m unittest discover -s web/tests -p test_book_content.py -v
python3 web/scripts/book_content.py
```

To export content-addressed JSON into a private staging directory:

```sh
python3 web/scripts/book_content.py --out /private/tmp/lumen-release-staging
```

The CLI rejects destinations within `web/dist`. Keep staging outside the public
runtime and outside committed bulk production directories. Text filenames carry
the SHA-256 of their exact JSON bytes. Repeated builds preserve identical files;
exclusive atomic writes prevent concurrent builders from silently overwriting
an immutable file. Exports check resolved descendant containment before creating
anything and reject child symlinks. Directory-descriptor traversal also prevents
a symlink introduced during writing from redirecting private data outside the
staging root. The registry and inventory are private production artifacts.
A future public book index should reference individual chapter files rather
than downloading this full registry at reader startup.

## Canonical registry

`load_project_registry(root, previous=None)` reads the five v6 manuscript parts,
both generation manifests, and the existing pilot. It verifies recorded source
SHA-256 values, complete chapter order, the same faithful ordered narration
across voices, and correspondence between prepared narration and displayed
prose. Clip IDs and chunk boundaries remain specific to each edition; a faithful
retake or split preserves the shared registry and anchors. Opening credits keep
the manuscript epigraph as a separate paragraph independently of audio chunks.
It produces 91 ordered tracks and an 89-entry story table
of contents. Opening credits and closing credits have distinct track kinds.

Each paragraph retains its manuscript markdown, faithful display text, emphasis
ranges, and sentence character offsets. Scene breaks and subsection headings
remain explicit blocks. Spoken part/chapter introductions remain separate from
the manuscript paragraphs. The epilogue division remains attached to its first
chapter. Display text keeps written technical labels and countdowns; comparison
alone uses the explicit pronunciation substitutions already present in
`Draft/v6/export_audiobook.py`. ASR normalization is a separate future layer.

An anchor is qualified by manuscript version and chapter:

```text
v6:chapter-001:p001
v6:chapter-001:p001-s01
v6:chapter-002:p001-s01
```

Sentence segmentation preserves dotted initialisms and personal initials within
prose, including `D.C. residence`, `U.S. Supreme Court`, and `Ursula K. Le Guin`.
Clear following sentence openers, such as `D.C. He`, retain the true boundary;
paragraph ends remain boundaries. Ambiguous abbreviation contexts still need
editorial review before anchor/timing approval. Time abbreviations use a capitalized
following subject as a new sentence boundary, including proper names, while
explicit timezone phrases such as `a.m. London time`, `p.m. Central European
Time`, and `a.m. CET` remain within the sentence. Explanatory `e.g.`/`i.e.` and
honorifics retain their continuation behavior. Tests cover every timezone pattern
in the current manuscript and all observed new-subject time boundaries. The
current private registry has 12,922 sentence anchors, and the existing 60 pilot
sentence aliases are unchanged.
These exports remain pending; the corrected segmentation changes their content
hashes before final timing maps are created.

Pass the prior registry with `--previous` when rebuilding. A reused sentence ID
with different prose is rejected. A revised manuscript needs a new version and
an explicitly reviewed migration map; positional IDs alone cannot determine
which rewritten sentence represents a listener's saved place.

The `legacyAliases.chapter-001-pilot` map verifies all existing pilot paragraphs
and sentences before emitting aliases. `completedExcerpt` points to the final
pilot sentence at fraction 1.0, with `trackCompleted: false`. This lets a future
full-book controller continue beyond the five-minute passage without marking
the full chapter complete. It does not alter existing browser bookmarks.

## Narrator recording inventory

`load_recording_inventory(root, registry)` reconciles the 182 edition-track
records, checking generation/delivery/mastering metadata hashes, selected
narration identity, MP3 paths and byte counts, checkpoint/QA bindings, and the
decoded MP3 sample clock. Each recording includes its narrator ID, SHA-256,
decoded duration, byte count, warnings, and private relative production path.
It also records ordered `selectedClips` (IDs, exact request SHA-256s, raw
UTF-8 text SHA-256s, logical raw source paths, expected raw audio SHA-256s, and
current byte counts), `selectionSha256`, `generationManifestSha256`, and
`masterIdentitySha256` for that edition. Shared narration hashes depend on
ordered faithful prose rather than selected clip IDs or chunk topology.

Selected request hashes are recomputed with the production encoding of
`model + voice + style + text`, and each selected voice must match its edition.
Ordered selected IDs and request hashes must match checkpoint identity inputs.
The master chapter/title identity must match the canonical track and delivery
row. Its compact-JSON production digest (without a trailing newline) must agree
across the checkpoint, delivery binding, and QA. The mastering report must be
complete and bind the current generation manifest. Merely refreshing outer
container hashes cannot relabel an old master as a new selected take; a retake
requires an affected chapter rebuild and coherent provenance.
Both QA gates, `technical_ceilings_and_format_checks_passed` and coordinator
`duration_and_boundary_checks_passed`, must be explicitly true. Failed or
missing technical checks reject the inventory even if outer hashes agree.

Each selected manifest output and checkpoint input path must name the same
logical `Audiobook/<edition>/raw/<selected-id>.wav` file. Historical absolute
paths are reconciled against the caller's root without reading their old
locations. The current file must exist inside that root. Delivery
`source_binding.raw_inputs` must equal the checkpoint's ordered path, request
hash, and raw audio hash bindings. A changed output filename, missing raw take,
or external symlink cannot inherit an earlier take's master lineage.

The caller's `root` owns the current logical asset path:
`Audiobook/<edition>/mastered/<track>.mp3`. Historical absolute paths retained in
delivery manifests are checked for that exact logical edition/filename suffix,
then reconciled against files under the current root. Moving or restoring the
project does not rewrite production metadata or its checkpoint hashes. Wrong
editions, filenames, traversal components, and local symlinks escaping the root
are rejected. Physical byte verification reads the current-root file; it never
falls back to an asset left at the historical path.

Every inventory metadata file must also resolve inside the selected root before
reading and again before hashing: generation/delivery manifests, the chapter
list, mastering report, per-track QA, and checkpoints. An external symlink is
rejected even when its target has identical bytes and would match the recorded
SHA-256. Historical path strings remain provenance evidence; they do not grant
permission to read files outside the selected project root.

The duration comes from decoded MP3 samples, rather than the lossless assembly
clock used by historical delivery-manifest fields. Records initially have
`audioHashVerified: false`, `audioVerificationSha256: null`, `rawSourceHashesVerified: false`,
`rawSourceVerificationSha256: null`, `contentApproval: pending`,
`timingApproval: pending`, and `publicationStatus: pending`. This metadata
inspection checks raw file ownership, existence, and size, without reading all
raw audio or 2.66 GB of mastered audio. Matching metadata or byte counts alone
does not prove that the current raw bytes still match their master inputs.

`verify_recording_file(recording, root)` verifies the actual file byte count and
streams its SHA-256, returning a copy with `audioHashVerified: true` and
`audioVerificationSha256` bound to that verified recording hash. Publication
requires the proof hash to match the current audio hash; retaining a true flag
after re-encoding cannot reuse the earlier proof. It does not grant content
approval. Re-encoding a chapter creates a new recording identity;
its final encoded bytes must be inventoried and aligned again.

`verify_recording_sources(recording, root)` streams only that recording's
selected raw files and compares their sizes and SHA-256s with the inventoried
master inputs. It returns a copy with `rawSourceHashesVerified: true` and
`rawSourceVerificationSha256` bound to the current `selectionSha256`; the
original inventory remains unchanged. Same-size tampering fails this check.
This focused verification can run for one rebuilt chapter without rescanning
unrelated takes. It grants neither listening approval nor publication approval.

## Timing map validation

`validate_timing_map(track, recording, timing, require_approved=False,
alignment_verification=None)` requires
the track ID, manuscript version, canonical text hash, narrator ID, final audio
SHA-256, and decoded duration to match. Sentence cues must contain every
canonical anchor exactly once in order. **Schema 1** requires finite, positive,
non-overlapping intervals for every sentence. The map declares `coverage.introEnd` as the
first sentence onset and `coverage.tailStart` as the final sentence end. These
fields identify leading/trailing non-prose intervals for review; they do not
prove that a spoken introduction or tail has correct content.

For a schema-1 publication gate, use `require_approved=True`. Both `approvals.content`
and `approvals.alignment` must be explicit reviewer records bound to the same
audio and text hashes, with a timezone-bearing review timestamp. An
`accepted-with-note` disposition needs a nonempty note. The actual mastered audio
hash must have been verified, and the selected raw source proof must be verified
and bound to the recomputed current selection hash, including source paths,
expected audio hashes, and byte counts. Missing or stale source proof keeps the
recording pending. The alignment approval additionally stores
`timingSha256`, computed with `timing_content_hash(timing)`. This digest includes
every field of the timing map except its `approvals` metadata. Editing a sentence
cue, coverage interval, clock offset, or other map field invalidates alignment
approval and requires review of the newly hashed map. Updating reviewer metadata
does not alter the reviewed timing payload. Keep these approval records in the production
audit trail; this validator checks their structure and binding, rather than
authenticating a reviewer or manufacturing approval.

Example map shape:

```json
{
  "schemaVersion": 1,
  "trackId": "chapter-001",
  "manuscriptVersion": "v6",
  "textSha256": "<canonical-track-sha256>",
  "narratorId": "charon",
  "audioSha256": "<final-encoded-audio-sha256>",
  "duration": 700.0,
  "sentences": [
    {"sentenceId": "v6:chapter-001:p001-s01", "start": 5.0, "end": 13.0}
  ],
  "coverage": {"introEnd": 5.0, "tailStart": 13.0},
  "approvals": {}
}
```

The example is abbreviated and will fail validation for a complete chapter.
The registry/inventory builder emits no final timing maps. The separate local
finalizer described below creates measured maps from bound ASR evidence.

### Schema 2: truthful synchronization gaps

Schema 2 keeps every canonical sentence in order. A measured record explicitly
has `syncStatus: "measured"` and positive, finite, non-overlapping `start`/`end`.
An uncertain record has `syncStatus: "unavailable"`, explicit `start: null` and
`end: null`, a neutral reason, selected clip `evidenceIds`, and a diagnostic
`recheckWindow`. Allowed reasons are `no-positive-duration-ASR-anchor`,
`overlapping-ASR-interval`, and `low-confidence-ASR-anchor`. A reason never
rewrites or explains the storyline.

The recheck window has `scope: "neighbor-recheck-only"`, the preceding and
following measured sentence IDs (or null at the edges), and bounds equal to
that preceding sentence's end and following sentence's start. Recording edges
use 0/duration. These are **recheck bounds, not a sentence cue**; zero-width
diagnostic bounds are permitted. The reader cannot seek/highlight an unavailable
sentence or map another voice's bookmark through it. Faithful prose remains
visible. Coverage uses the first/last measured cue, or 0/duration when every
sentence is unavailable. Schema 1 continues to reject all gaps.

### Technical alignment verification

The author has accepted the existing 192 kbps recordings as-is. That decision
does not manufacture a full human alignment review. A schema-2 map can use an
`approvals.alignment` record with `status: "verified"` and method
`hash-bound-local-asr-map-rebuild-v1` instead of claiming human `approved` status.
This separate technical record binds exact audio, text, timing, ordered raw
selection, master identity and generation-manifest hashes, a timezone-bearing
verification timestamp, and the private verification report's path/SHA-256.

`validate_timing_map(..., require_approved=True,
alignment_verification=report)` requires the actual report, selected cache
evidence, source/QA bindings, accurate measured/unavailable counts and completed
physical/cache/rebuild checks. A bare verified flag cannot pass. Human content
approval remains mandatory and source-bound. Schema 1 still requires its
original human alignment approval; schema 2 also supports that route.

Before export, `verify_technical_alignment` independently reads/hashes the
report and every selected ASR cache, checks raw request/text/decoder identities,
the current generation/checkpoint/QA lineage and physical MP3/raw bytes, then
rebuilds the map from observed word intervals. It requires the rebuilt timing
digest and report to agree. Refreshing a forged map/report hash cannot replace
this check. Reports explicitly say `humanAlignmentApproval: "not-claimed"`.

`web/scripts/finalize_release_timings.py` reads the ignored
`Audiobook/author-audit/web-release/release-inventory.json`,
`audio-release-report.json` and existing `author-approval.json`. It calls the
existing preparation APIs to recompute source-bound evidence, merges observed
cues and unavailable gaps, and saves immutable per-recording timing/technical
reports plus the private `finalized-timings.json` and
`timing-finalization-report.json` convenience pointers. It performs no inference,
paid API calls, regeneration, uploads or deployment. Missing caches, stale
author/source bindings, external symlinks and changed physical bytes fail closed.
Only a successful complete recording set updates the final convenience pointer.

```sh
python3 web/scripts/finalize_release_timings.py --dry-run
python3 web/scripts/finalize_release_timings.py
python3 -m unittest discover -s web/tests -p test_finalize_release_timings.py -v
```

This is an explicit full selected-source checksum operation, approximately
7.95 GB of local reads for the current 182 MP3s and 496 raw WAVs. Run after
alignment refinement and source changes have finished. Its `local-finalized`
status is distinct from remote authorization, delivery verification or launch.
`--dry-run` rebuilds the input report's bound cached evidence without artifact
writes or bulk MP3/raw reads, and explicitly reports
`physicalSourcesVerified: false`. It is suitable for checking actual schema and
gap coverage while a separate refinement worker is still producing new caches;
it does not replace the final physical verification pass.

`resolve_scenes(track, recording, timing, scenes)` resolves shared canonical
scene triggers against that recording's independently validated clock. Shared
scene records contain `id`, `sentenceId`, and `src`, with no narrator seconds.
It rejects unknown anchors, unavailable synchronization gaps, duplicate IDs,
and reversed trigger order. Every reveal anchor must have a measured clock. This
function does not grant artwork, identity, or reveal approval.

## Remaining roadmap gates

- Complete author listening decisions and scoped regeneration/rechecks.
- Freeze approved encoded files, verify their physical hashes and each selected
  raw source's selection-bound proof, and create/review each voice's independent
  final-file timing maps.
- Choose the publication/access policy, storage account, and funded media
  budget before uploading the full unpublished book.
- Implement the reader's chapter/narrator controller and verified bookmark
  migration using the canonical aliases.
- Approve artwork models, paintings, and narration reveal cues after the audio
  gate; verify real Android playback and the release's CDN byte ranges.

These gates remain pending. The existing pilot stays usable throughout this
work.
