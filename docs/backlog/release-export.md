# Approved release export

`web/scripts/release_export.py` prepares immutable reader JSON and a private
upload inventory. It does not upload files, change the live pilot, create
approval records, align recordings, or generate characters or paintings.

Run the focused contract tests:

```sh
python3 -m unittest discover -s web/tests -p test_release_export.py -v
```

## Inputs and API

```python
build_release(root, registry, inventory, timing_maps, scenes_by_track, plan, staging)
```

The registry and inventory come from the [canonical content contract](content-contract.md).
The exporter verifies current manuscript hashes, rebuilds canonical prose from
the selected-root manuscripts and generation manifests, and reconciles the
supplied registry and inventory with that evidence. Private annotations are
excluded from public prose; self-rehashing rewritten prose under an unchanged
source hash fails. Existing pilot aliases must match the current pilot when it
is present. Cached physical-verification flags are never trusted.

`timing_maps` is an object keyed by track ID, then narrator ID. Each value is a
complete reviewed timing map from the content contract. Required maps must be
present. Every supplied selected-voice map needs human content approval bound
to its exact text and final audio. Schema 1 retains mandatory human alignment
approval. Schema 2 can use either human alignment approval or a separate actual
technical verification report bound to the map and current sources. The exporter
then freshly verifies the current mastered bytes and all selected raw sources.
For technical alignment it also rehashes each exact ASR cache, checks its
source/master/QA/decoder identity and independently rebuilds the measured map.
It passes the actual report to `validate_timing_map(..., require_approved=True)`.
An arbitrary `verified` flag or self-rehashed map cannot satisfy these checks. Approval or lineage
failure stops preparation before any staged file is written.

An explicit plan selects a release subset and its required voices:

```json
{
  "schemaVersion": 1,
  "releaseId": "approved-beta-1",
  "accessModel": "private",
  "appDataURLbase": "https://reader.example/releases/",
  "mediaURLbase": "https://media.example/lumen/",
  "author": "Rob Xie",
  "defaultTrackId": "chapter-001",
  "defaultNarratorId": "charon",
  "narrators": [
    {"id": "autonoe", "label": "Autonoe"},
    {"id": "charon", "label": "Charon"}
  ],
  "tracks": [
    {"id": "chapter-001", "extentId": "chapter-001-full", "requiredNarratorIds": ["charon"]}
  ]
}
```

These are placeholder origins, not a publication configuration. URL bases are
required absolute HTTPS URLs; credentials, queries, fragments, encoded paths,
and traversal are rejected. References are absolute and use content-hash
filenames. Origin fields contain origins alone, while URL bases retain their
explicit path prefixes. An omitted `extentId` defaults to `<track>-full`. A full
chapter cannot reuse a legacy pilot extent ID.

`accessModel` must explicitly be `private`, `public`, or `authenticated`. Selecting
an access model does not implement it or authorize remote publication. Every
selected track must have at least one approved voice, and the default recording
must be ready. Track order follows the canonical book. Omitted optional maps
produce `pending` for an existing edition or `unavailable` for an absent edition;
neither status exposes a recording URL. A supplied optional map is held to the
same approval and verification gates as a required map.

## Reader and delivery contracts

The public book manifest contains:

- `schemaVersion`, `bookId`, `manuscriptVersion`, `releaseId`, default track and
  narrator IDs, and narrator `{id,label}` records.
- Selected tracks with `id`, `title`, `part`, `partTitle`, `kind`, and recordings
  indexed by narrator ID. A ready recording has `{status:"ready",url,audioSha256}`.
  Pending and unavailable entries contain only their status.
- The supported registry `legacyAliases` unchanged, including
  `chapter-001-pilot` paragraph/sentence mappings and `completedExcerpt` with
  `trackCompleted:false`. Unsupported private fields in aliases are rejected.
- `appOrigin` and `mediaOrigins`, including the app origin for data delivery checks.

Each ready recording points to an immutable chapter JSON payload. It includes
`bookId`, `manuscriptVersion`, canonical `chapterId`, stable `id` and
`readingExtentId`, `readingExtent:"full"`, title/author/part/kind/narration labels,
and decoded duration. Its audio record contains absolute `src`, `sha256`,
`narratorId`, `decodedDuration`, duration, byte count, and format. Paragraphs retain
faithful markdown, text, emphasis, headings, and qualified sentence IDs/offsets,
with that voice's measured `start`/`end` cues. A schema-2 payload explicitly
labels sentences `syncStatus: "measured"` or `"unavailable"`; unavailable
sentences retain faithful text with null start/end. Paragraph boundaries use
the first and last measured sentences, or both null when none are measured.
Diagnostic recheck windows, evidence IDs and technical reports stay private.
Canonical blocks retain scene breaks and headings.
No narration timestamps are copied between voices.

Public objects exclude local paths, raw selections, checkpoints, author audit
notes, reviewer records, and cast inputs. A text-only beta has `scenes:[]`.

The private upload inventory is `{schemaVersion,releaseId,accessModel,appOrigin,
mediaOrigins,assets}`. Each asset has `url`, `sha256`, `bytes`, `contentType`,
`immutable:true`, local `sourcePath`, and three ordered range samples. For `n`
bytes, sample width is `min(1024,n)` and offsets are `0`, `(n-width)//2`, and
`n-width`; ends are inclusive. Each sample hashes those exact local bytes. Small
assets can have identical windows. The inventory's local paths are operator
inputs and must never be served publicly. It describes existing mastered media
and newly staged JSON; it does not duplicate bulk media or include itself as an
upload asset.

## Optional reviewed scenes

Character and image expansion remains held. The exporter can consume existing
approved paintings when a release explicitly includes them. `scenes_by_track`
maps track IDs to ordered scene lists. Each scene requires `id`, canonical
`sentenceId`, owned local `sourcePath`, physical image `sha256`, plain `title`,
plain `alt`, and an `approval` record. The record contains an approved or
accepted-with-note status, reviewer, timezone-bearing review timestamp, and
`sceneSha256=scene_content_hash(scene)`; accepted-with-note needs a note. This
review binds the image hash, canonical reveal trigger, and visible title/alt.
Changing any bound field invalidates it. Review must cover painting continuity
and the absence of an early reveal.

Scenes require optimized WebP paintings, matching the reader and remote delivery
contract. PNG/JPEG source paintings must be optimized and the resulting WebP
hash reviewed before inclusion. The image is freshly hashed. Public scene objects contain an immutable `src`,
approved title/alt (`description` mirrors alt for the reader), canonical trigger,
and narrator-resolved start/end. A scene trigger must use a measured sentence;
an unavailable gap has no reveal timestamp. Local paths and approvals stay private. Missing
or malformed scene approval fails; it is never replaced with a generated image
or an assumed approval.

## CLI and promotion gate

```sh
python3 web/scripts/release_export.py \
  --registry /private/tmp/registry.json \
  --inventory /private/tmp/inventory.json \
  --timings /private/tmp/reviewed-timings.json \
  --plan /private/tmp/release-plan.json \
  --out /private/tmp/lumen-approved-staging
```

Use `--scenes` only for an existing reviewed scene mapping. With no `--out`,
staging defaults to `/private/tmp/lumen-release-<releaseId>`. All exports reject
`web/dist` destinations, including symlink redirection. Content-hash writes are
exclusive and idempotent; existing different bytes at an immutable path fail.
Keep staging outside production sources and committed bulk media directories.

The result provides the public manifest URL, local manifest path/hash, and local
upload inventory path. Its status is `staged`. No network requests occur.
The author has accepted the current 192 kbps audio as-is. Full-book preparation
uses `finalize_release_timings.py` to create source-bound schema-2 maps and actual
technical reports, preserving uncertain passages as explicit synchronization
gaps. This route does not claim a full human listening/alignment review. The
exporter requires those actual artifacts and freshly verifies them before a
recording is ready. Tiny fixtures exercise both human-approved schema 1 and
technically verified schema 2 without making production-listening claims.

Promotion remains a separate operation. Before uploading, confirm the chosen
access policy and funded storage, implement that policy for prose/timings/media,
and pass remote exact-byte/CORS/range verification. `private` and `authenticated`
plans must never be copied into a public static app or public object bucket.
The manifest URL becomes usable only after the intended delivery controls and
verification gates pass.

## Prepared private release

The local `lumen-private-192-v1` export is complete: 91 tracks in both voices,
365 assets totaling 2,667,477,164 bytes. It references the 182 unchanged MP3s
(2,657,554,540 bytes) and creates 183 JSON objects (9,922,624 bytes). Female
narration starts by default with opening credits; every scene list is empty
for the authorized generic-placeholder release. All 25,844 canonical sentence
positions are represented: 25,702 measured and 142 explicitly unavailable.

The exporter freshly verified actual masters, selected raw sources and exact
ASR evidence again. The local manifest SHA-256 is
`4b12eaa91a7900e075c607245d3e23977dacd680a7d4c427d3c1463b448b8dcf`;
the private upload-inventory SHA-256 is
`bd6ccb66fb5b1e04f98e19d5a3bbe26d1d4f5ae847a67090af0fffa7748a6f5f`.
The private release-export-verification SHA-256 is
`db08052e2f61783c81dc6c14ef9937b976f06cd001e7c37fb3e4b43be29cf90a`.
All 1,095 inventory range samples were checked against their source bytes.
These records prove local preparation; remote upload and authorization still
require the separately verified private-delivery operation.
