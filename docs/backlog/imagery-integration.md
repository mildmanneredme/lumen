# Generated imagery integration

Branch: `codex/incorporate-generated-imagery`. Decision recorded 11 October 2026, Asia/Bangkok.

Use the existing **private Cloudflare R2 bucket `lumen-private`** for selected reader paintings. The authenticated `/api/assets/` route already grants access to WebPs through the Cloudflare Worker; image bytes stream directly from Cloudflare. Keep this access model and the production origin. No additional public bucket, image proxy or Cloudflare Images service is needed.

The 89 story chapters have 1,379 selected paintings. Their full-size WebPs total 310,814,662 bytes, and their 960×640 phone versions total 117,328,528 bytes: 428,143,190 bytes combined (408.31 MiB). Keep original PNGs, casting sheets, rejected candidates, prompts and review records in private production storage. The existing public pilot paintings retain their current URLs.

R2 supports private Worker access, streaming reads and conditional writes. Direct Internet egress has no R2 charge; storage and operation allowances apply across the account. Public bucket domains would bypass the reader's access model and stay disabled. See Cloudflare's [Workers API](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/), [public-bucket configuration](https://developers.cloudflare.com/r2/buckets/public-buckets/) and [pricing](https://developers.cloudflare.com/r2/pricing/).

## Branch implementation

`web/scripts/prepare_illustrated_scenes.py` imports the selected-scene manifests into a private draft accepted by the release exporter's `--scenes` argument after review. It verifies canonical sentence hashes, scene order and both WebP hashes. The audit reports each narrator's measured trigger against that recording's timing map; unavailable anchors retain a null clock, and shared triggers are flagged as publication blockers. It preserves production selections and creates no release approvals.

Draft reader labels are neutral. Original titles and descriptions remain in the private audit because some contain prompt instructions, casting references or future-story hints. Explicit reader metadata can replace these defaults and carry the existing hash-bound review contract.

The release exporter optionally includes `phoneSourcePath` and `phoneSha256`. Review binds the phone image hash as well as the full painting, sentence, title and alt text. Both images enter the immutable private inventory; reader payloads expose only `src` and `phoneSrc`. The reader selects a suitable variant for its display and loads the active painting and the next one. Existing single-image scene payloads remain supported.

A failed current image gets one automatic retry. A terminal failure clears stale artwork and can recover when the browser reconnects. Image errors leave narration status and playback intact.

Private artwork continues to bypass service-worker Cache Storage. Sign-out and rewind clear displayed paintings; updates wait for existing tabs to close. The shell cache version is bumped for the runtime change.

## Current publication gaps

The final `lumen-private-192-v4` timing maps have the following exact selected-anchor coverage:

| Coverage | Paintings |
| --- | ---: |
| Measured in both narrator editions | 1,180 |
| Male edition only | 62 |
| Female edition only | 73 |
| Neither edition | 64 |

Thus 199 paintings across 78 chapters need a measured trigger in at least one edition. Do not estimate seconds or silently shift an anchor. Chapters 12, 15, 16, 19, 29, 30, 36, 55, 65, 73 and 80 have all selected anchors measured in both editions (152 paintings). All 1,379 selections still have pending reader release approvals, distinct from the completed technical visual checks.

Two pairs also share a single trigger: Chapter 23's gyoza and cat-socks memory paintings, and Chapter 48's packed-bag and checkout paintings. The reader would display only the last painting at an identical clock. The audit preserves and flags all four scenes; release export rejects duplicate anchors until reviewed distinct triggers or an explicit consolidated selection resolves them.

Prepare the private draft and report from the project root:

```sh
python3 web/scripts/prepare_illustrated_scenes.py \
  --registry asset-staging/lumen-illustrations-v1/canonical-registry.json \
  --selections asset-staging/lumen-illustrations-v1 \
  --timings Audiobook/author-audit/web-release/revisions/lumen-private-192-v4/finalized-timings.json \
  --out Audiobook/author-audit/imagery-integration-v1
```

Preparation does not upload files or change the live site. Resolve timing gaps and review reader labels before exporting a publishable scene map.

Local verification passed 20 importer checks, 34 release-export checks, 98 reader/backend/deployment checks, the private-cache shell check, 31 focused Chrome imagery checks and 133 existing Chrome reader checks. These checks use local fixtures; they do not claim hosted artwork acceptance.

## R2 publication work

Create a new immutable release and index when the reviewed imagery is ready. The temporary upload Worker, its build configuration and its operator adapter currently restrict writes to `lumen/releases/lumen-private-192-v4/`; configure and validate a new exact release prefix before uploading. Preserve the old release for rollback.

The current uploader places the entire inventory beneath each release prefix. Prefer extending release preparation to reuse independently verified prior immutable audio objects, uploading new paintings and chapter/manifest JSON only. The serving index already supports prior audio keys; no audio regeneration or bookmark migration is required when recording hashes and canonical sentence IDs remain the same.

Upload and independently verify image bytes before publishing the new index. Then verify authenticated image loading, both narrator clocks, seeking, bookmark restoration, phone layouts, sign-out and private-cache exclusion on a protected candidate. Production promotion remains a separate action through the existing Vercel project. See [Cloudflare delivery](cloudflare-delivery.md) for the established release procedure.
