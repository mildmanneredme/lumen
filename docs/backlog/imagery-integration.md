# Generated imagery integration

Branch: `codex/incorporate-generated-imagery`. Decision recorded 11 October 2026, Asia/Bangkok.

Use the existing **private Cloudflare R2 bucket `lumen-private`** for selected reader paintings. The authenticated `/api/assets/` route already grants access to WebPs through the Cloudflare Worker; image bytes stream directly from Cloudflare. Keep this access model and the production origin. No additional public bucket, image proxy or Cloudflare Images service is needed.

The 89 story chapters have 1,379 selected paintings. Their full-size WebPs total 310,814,662 bytes, and their 960×640 phone versions total 117,328,528 bytes: 428,143,190 bytes combined (408.31 MiB). Keep original PNGs, casting sheets, rejected candidates, prompts and review records in private production storage. The existing public pilot paintings retain their current URLs.

R2 supports private Worker access, streaming reads and conditional writes. Direct Internet egress has no R2 charge; storage and operation allowances apply across the account. Public bucket domains would bypass the reader's access model and stay disabled. See Cloudflare's [Workers API](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/), [public-bucket configuration](https://developers.cloudflare.com/r2/buckets/public-buckets/) and [pricing](https://developers.cloudflare.com/r2/pricing/).

## Branch implementation

`web/scripts/prepare_illustrated_scenes.py` imports the selected-scene manifests into a private draft accepted by the release exporter's `--scenes` argument after review. It verifies canonical sentence hashes, scene order and both WebP hashes. The audit reports each narrator's measured trigger against that recording's timing map; unavailable anchors retain a null clock, and shared triggers are flagged as publication blockers. It preserves production selections and creates no release approvals.

Reader metadata is source-bound and uses neutral numbered titles, physical descriptions and mode-aware fallback descriptions. The cleaned collection contains no production instructions or casting names. Original titles and descriptions remain in the private audit because some contain prompt instructions, casting references or future-story hints. Metadata preparation does not create painting approvals or claim a new visual inspection of every painting.

Reviewed `--corrections` records resolve competing triggers and unsafe timing gaps by selecting a later canonical sentence after the depicted event has been narrated. Each correction binds the original sentence and full/phone image hashes, the target sentence text and a narrative review hash. The original selections and all paintings are preserved.

`recover_scene_timings.py` supplies separate image-onset clocks where a baseline sentence start is unavailable. It checks exact three-to-six-token prefixes unique across the chapter, inside the original measured-neighbor window. Bounded recognition uses the installed offline medium model without a manuscript prompt. Retained raw recognition outputs, model files, raw/mastered audio and baseline caches are hash-bound and freshly checked. Export reproduces these cues and rejects equal or reversed starts. No sentence ending is estimated: uncertain prose stays unhighlighted and has no seek control.

Every contributing recognized word must fit its measured window and the selected prefix or phrase endpoint. Numeric normalization retains the coverage of all spoken words while preserving the actual final word's endpoint. Short sentence prefixes may use following context for corroboration, but their own words must remain inside the original sentence gap.

The final painting in Chapter 60 needs an explicitly reviewed phrase endpoint
because its first-word sequence is unavailable in one voice and there is no later
safe sentence. A distinct phrase-end proof uses six exact canonical words after
the depicted action is disclosed, with no name aliases or estimated seconds.
Its private request binds the selected manifest, full/phone paintings,
canonical text and token span; both preparation and export check those painting
bindings before applying the cue. The other voice retains its original measured
sentence trigger.

The release exporter optionally includes `phoneSourcePath` and `phoneSha256`. Review binds the phone image hash as well as the full painting, sentence, title and alt text. Both images enter the immutable private inventory; reader payloads expose only `src` and `phoneSrc`. The reader selects a suitable variant for its display and loads the active painting and the next one. Existing single-image scene payloads remain supported.

A failed current image gets one automatic retry. A terminal failure clears stale artwork, retains an already displayed version of the current painting, and can recover when the browser reconnects. Hidden artwork starts no painting loads; showing it selects the current narration scene and visible display size, including when hiding interrupted a crossfade. Image errors leave narration status and playback intact.

Private artwork continues to bypass service-worker Cache Storage. Sign-out and rewind clear displayed paintings; updates wait for existing tabs to close. The shell cache version is bumped for the runtime change.

## Baseline findings and correction workflow

The preserved `lumen-private-192-v4` sentence maps originally provided the following exact selected-anchor coverage:

| Coverage | Paintings |
| --- | ---: |
| Measured in both narrator editions | 1,180 |
| Male edition only | 62 |
| Female edition only | 73 |
| Neither edition | 64 |

The original selections therefore had 263 unavailable scene/voice anchors across 199 paintings and 78 chapters. Separate measured image cues and explicit narrative corrections address these gaps without changing the original sentence maps. All 1,379 selections still have pending reader release approvals, distinct from the completed production checks and these technical fixes.

Two original pairs shared a trigger: Chapter 23's gyoza and cat-socks memory paintings, and Chapter 48's packed-bag and checkout paintings. Reviewed after-disclosure corrections now give both paintings in each pair a distinct canonical trigger. The audit flags competing triggers; the exporter and reader reject non-increasing clocks.

Prepare the private draft and report from the project root:

```sh
python3 web/scripts/prepare_illustrated_scenes.py \
  --registry asset-staging/lumen-illustrations-v1/canonical-registry.json \
  --selections asset-staging/lumen-illustrations-v1 \
  --timings Audiobook/author-audit/web-release/revisions/lumen-private-192-v4/finalized-timings.json \
  --out Audiobook/author-audit/imagery-integration-v1
```

Add the source-bound `--metadata`, reviewed `--corrections` and measured
`--scene-timings` inputs from the ignored `Audiobook/author-audit/imagery-fixes-v1/`
directory for the corrected draft. The final private audit records their exact
paths and hashes. Preparation does not upload files or change the live site.
Painting review remains required before exporting a publishable scene map.

The corrected private draft validates all 1,379 paintings across 89 chapters and
178 narrator/chapter combinations. All 2,758 reveal clocks are measured and
strictly increasing; no selected painting has a missing clock or competing
trigger. There are 50 source-bound canonical corrections and 197 independent
image-only cues (196 exact prefixes and one reviewed phrase endpoint). The
original manuscript, audio, sentence maps and selected painting files remain
unchanged. Original unresolved targets stay in the recovery evidence when a
reviewed correction moves their painting to a different measured sentence.

The final private packet is under
`Audiobook/author-audit/imagery-fixes-v1/`. It includes `reader-metadata-v4.json`,
`anchor-corrections-coverage-v1.json`, source/recognition review records, the
`scene-clocks-coverage-final-v1/` combined clock index and `prepared-coverage-final-v1/`
draft/audit. The final audit SHA-256 is
`47e0cea47c2ef84ee3ce84dfaecfc29e3b19b6363dce191c1d71c957f3b4f376`;
the strict 178-recording clock validation SHA-256 is
`5554d33681bb3f1a5a0171a23b0a1522b2c7412ef1b2338e36057b80d03910d0`.
These files remain ignored and private. All 1,379 painting release approvals
remain pending; technical narrative corrections do not supply author aesthetic
approval.

Local verification covers the importer, source-bound clock recovery, release
export, immutable R2 uploads, actual local workerd/R2 behavior and Chrome reader
regressions. The focused Chrome imagery suite passes 54 checks, including both
voices, null prose timing, phone variants, invalid/equal/reversed clocks, image
retries, rewind, sign-out and private-cache exclusion. These checks use local
fixtures and do not claim hosted artwork acceptance.

## R2 publication work

Create a new immutable release and index when the reviewed imagery is ready. The temporary upload Worker, its build configuration and operator adapter now accept an explicitly selected exact release prefix. Preserve the old release for rollback.

The uploader can reuse explicitly selected prior immutable audio objects while uploading new paintings and chapter/manifest JSON. Prior releases are read-only and limited to hashed MP3 keys. Reuse requires fresh complete local and remote SHA-256 verification, three sample ranges and unchanged final object metadata. Missing or changed audio fails; it cannot be recreated under the old prefix. Upload budgets exclude reused audio, while verification budgets include the complete reads. No audio regeneration or bookmark migration is required when recording hashes and canonical sentence IDs remain the same.

Upload and independently verify image bytes before publishing the new index. Then verify authenticated image loading, both narrator clocks, seeking, bookmark restoration, phone layouts, sign-out and private-cache exclusion on a protected candidate. Production promotion remains a separate action through the existing Vercel project. See [Cloudflare delivery](cloudflare-delivery.md) for the established release procedure.

The uploader rechecks every local source after the final remote object and index metadata reads, before publishing the index. It checks sources again after index verification before returning success, including when resuming an existing release.
