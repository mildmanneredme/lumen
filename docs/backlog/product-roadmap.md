# Product roadmap: full Lumen audiobook web launch

Current access update, 11 October 2026: the code-compatible v18 reader is live at the stable address on deployment `dpl_CqJMWxpYAo9yvVmaqvLfnNzFxefj`, with reviewed implementation `e6b3702be263a7a31a2646e60138f5001c364df0`. Worker `3b3e2aad-c8e4-4460-b7c9-93f877b6330c` serves shared grant version 2. Live new-code access, old-code/session/media denial and normal Chrome 36/36 passed; the v4 book/audio/index, signing keys and expiry are unchanged. Earlier v17/v1 launch and A/B rollback evidence below is historical. Those frontends reject two-word codes, and restoring their Worker policy re-enables old access. Redeploy a compatible reader with the current registry; a compatible v2 rollback has not been exercised. [Current deployment and rotation evidence](../../web/data/deployment.json).

Status: private full-book launch live at the existing Vercel address after final protected-preview HTTP, normal-Chrome, separate native network-recovery checks and same-release production rollback/re-promotion. Full cloud-backup verification, physical Android, funded operating alerts and later artwork remain open. Updated: 11 October 2026. Primary device: Android with Chrome.

### Execution record — 10–11 October 2026

The author selected **local-only access for the narration audit** and requested character confirmation before further art production. Unfinished audio, manuscript exports, and author notes remain local.

- **Delivered: author audit.** [PR #1](https://github.com/mildmanneredme/lumen/pull/1) merged after two independent review rounds and fixes for stale review bindings and generation-input verification. Run `python3 web/scripts/audit_server.py` and open `http://127.0.0.1:8766/audit`. The queue contains 377 OpenAI candidates plus 755 unresolved/inconclusive local Charon candidates, with distinct evidence and audio clocks. Feedback persists with revision history; export groups explicitly requested corrections into source-take repair plans. Seventeen backend and eighteen Chrome behavioral checks pass. [Audit guide](../../web/audit/README.md).
- **Delivered: private P0/P2 foundation.** Reconciled 91 tracks, 89 story chapters, 12,922 chapter-qualified sentence anchors, 182 narrator recordings, and 60 pilot aliases. All 182 existing MP3 hashes matched their delivery manifests (2,657,554,540 bytes); all 496 selected raw WAV hashes also matched their master inputs (5,292,198,464 bytes). Timing validation binds the final audio, canonical text, and reviewed map hash; exports are immutable and reject symlink escapes into public assets. Faithful retakes retain canonical anchors, while selected-take and mastering identities must agree. Publication also requires physical raw-source verification bound to the selected take set. Fifty-five foundation tests passed at the reviewed PR #2 merge. [Content contract](content-contract.md).
- **Ready for author confirmation:** [23 character profiles and two provisional model sheets](character-confirmation.md). Seb's height discrepancy, Polk's age, and Daniel's wardrobe are explicit decisions. No model or appearance has been marked approved.
- **Author release decision:** current audio accepted as-is; no regeneration or further paid transcription required. Use all existing 192 kbps MP3s, female voice by default, generic illustration placeholders, and “Inspired by Rob Xie”. The private audience is approximately 50 readers. [Release specification](release-specification.md).
- **Access decision:** one memorable shared code for the private audience, with returning device sessions and revocation/rotation. The code stays in encrypted operator records; only its hash enters server configuration.
- **Prepared and independently reviewed:** invitation sessions/private Blob delivery ([PR #3](https://github.com/mildmanneredme/lumen/pull/3)), full-book navigation/resume/narrator switching ([PR #4](https://github.com/mildmanneredme/lumen/pull/4)), and source-bound technical timing/export/backup tools. 133 Chrome behavioral checks, 73 controller tests, 49 private-backend/operator and deployment checks, and the cache regression pass. The release pipeline has 351 passing release Python fixtures; 40 further actual-release Chrome checks validate all 182 payloads, canonical prose and ten representative MP3s. Physical phone acceptance and actual hosted-media verification are separate checks.
- **Timing evidence:** all 496 selected raw clips have local ASR caches. Across both recordings, 23,673 sentence positions have measured cues and 2,171 are explicitly unavailable. Uncertain passages retain their exact manuscript text and audio; they receive no invented seek/highlight time. Technical alignment verification does not claim a full human timing review.
- **Local export verified:** 365 immutable assets / 2,667,475,488 bytes prepared after fresh verification of actual masters, selected raw sources and exact ASR caches; all 1,095 inventory range samples match source bytes. No remote publication is implied.
- **Backup created and locally verified:** `lumen-backup-20261010-v4-1249` contains 6,340 files / 23,510,599,520 source bytes in a 20,495,430,382-byte ZIP. Full local hash verification and a seven-file restore passed. Drive confirms the versioned archive in the owner-only Lumen folder with the expected size; the checksum companion also matches downloaded bytes. Full cloud byte verification remains pending because the connector rejects files over 256 MiB. [Backup evidence and recovery](backup-recovery.md).
- **Cloudflare provisioned and uploaded:** [PR #9](https://github.com/mildmanneredme/lumen/pull/9) merged after independent review, 118 backend/operator/deployment checks, 27 actual workerd HTTP checks and a clear GitHub review. The private APAC Standard R2 bucket contains all 365 immutable assets plus the index (2,667,632,125 bytes). Every object passed a complete remote SHA-256 read; all 1,095 sample ranges matched. The temporary uploader was deleted after verification. Workers stays on its free plan and bucket public domains remain disabled. Live gateway verification passed 39 checks, all 182 audio header/grant bindings and a complete 35,239,853-byte chapter hash, including exact ranges, CORS and access denials. Direct native Chrome audio passed 18 further checks in both voices, including unbuffered seek, ±15 seconds and resume; it did not test the Vercel app UI or bookmarks. That earlier storage/gateway evidence predates the final-source launch recorded below. [Cloudflare delivery](cloudflare-delivery.md).
- **Historical protected HTTP verified:** 18/18 assertions at source `686be906692eb1c2a9069217a62f78016a427675`, covering 365 assets, 1,095 samples and the full largest-MP3 hash. [PR #10](https://github.com/mildmanneredme/lumen/pull/10), [PR #11](https://github.com/mildmanneredme/lumen/pull/11) and [PR #12](https://github.com/mildmanneredme/lumen/pull/12) merged after review. This earlier transport proof does not verify the new candidate or final app/native/production acceptance.
- **Native streaming and narrator race repaired:** [PR #14](https://github.com/mildmanneredme/lumen/pull/14) merged after seven red streaming regressions passed with 58 Node tests and 27+11 actual workerd checks. It removes per-chunk JavaScript asset layers while retaining exact metadata/length, cancellation and fresh access-policy checks. [PR #15](https://github.com/mildmanneredme/lumen/pull/15) merged as `363c67e5b02454c7b28bfc4013a7f10bc0e51fff` after independent/GitHub review and fixes a rapid narrator-then-chapter race; nine red browser regressions now pass in 148 installed-Chrome checks, with 73 controller tests and the private-shell cache check.
- **Final protected preview verified:** source `48f4b56865b1f6e472aa69bf171a4b346e9d0516`, deployment `dpl_DWQxdSRSQGsmtCStJ8KBVZ4EP4GZ`, [candidate](https://lumen-hju6gps33-robert-xies-projects.vercel.app/), stage receipt `17409d9e23434ddff33e9d1f89b319867546d4bd12e5cf988884599327de63ed`. Worker version `d0345c85-bc10-42a9-b94e-f1e1e277076a` at 100% and receipt `641937fa05cddd05f7d61eaaa6ad1aefced6ac7ddb4ea0cff56b22ea46bab648` bind the same source/index/preview origin, preserved access policy and disabled logging. Workers remains Free. Hosted HTTP passes 18/18, all 365 assets/1,095 range samples and the full largest-MP3 hash (`80b8f6de7354ef7bd32b2a8365ab7187b8bf274bcd2d6ddf2e33857fd13c2308`). Normal Chrome passes 36/36 with no browser/native issues (`c9bb48feba54862b809900dec02d1698e2f9dfe395d2295844c14695d298c057`); faithful manifest hash, controls, resume, private-cache behavior and emulated phone/iPad/desktop safe-area layouts match. A separate CDP-throttled/offline gate passes both voices with same-element queued-play recovery and saved-place preservation (`18c9f0589d699f66ebd31109b1d5469620a9411c86ae2cd1b7dae0b6e07682cb`); 39 requests/9,327,409 decoded bytes, no reload. Physical devices, shell-offline and full-book human listening are not claimed. Completed final preview QA tokens are revoked/cleared and Vercel protection is unchanged. [Evidence and limits](cloudflare-delivery.md).
- **Initial stable-origin launch and rollback acceptance:** the initial launch was at [lumen-phi-five.vercel.app](https://lumen-phi-five.vercel.app/), production B `dpl_FyFkovg79KUP2pvZ4sQSTysydpag`. All 54 runtime tuples match reviewed source `48f4b56865b1f6e472aa69bf171a4b346e9d0516` / identical merge `363c67e5b02454c7b28bfc4013a7f10bc0e51fff`. Actual A → B → rollback A → B passed with both voices and saved nonfirst chapter bookmarks (`fb0c5b7713d70f3c86211839a83af809432ebd6b3aefb281be5180317fdfadb0`). Final ordinary Chrome passed 36/36 on the stable site with no errors/native issues or preview token (`0e10badbddc906bd441ace464880c066378337d77d26db1d20cb962d0e2dcb87`). This is same-release operational rollback; pilot fallback and backwards compatibility are untested. [Private release operations](private-launch.md) records limits, support and incident ownership.
- **Remaining owner follow-ups:** first-48-hour/weekly usage and feedback observations, monthly spending ceiling/account alerts, repository privacy, Drive ZIP cloud checksum/remote restore beyond the connector's 256 MiB limit, physical Android and separate character/model/story artwork review (including PR #13). Later offline downloads, cross-device progress and other planned features remain later. No additional confirmation step or full-roadmap completion is implied.

The author has authorized a full private launch with placeholders after technical verification. Earlier public-beta/artwork and complete-listening gates below are superseded where they conflict with the recorded release specification. The private placeholder release is now live; unperformed checks below remain open where they are not superseded by this explicitly recorded scope.

## 1. Launch approach

Keep the existing Vercel reader and stable production address, [lumen-phi-five.vercel.app](https://lumen-phi-five.vercel.app/). Turn the single-chapter pilot into a book reader that loads one chapter and one narrator at a time. Publish approved chapter audio and artwork through object storage with CDN delivery. Generate all narration, timing maps, and artwork during production; the listener's browser plays prepared assets.

The first full-book release should provide both approved voices, faithful prose, synchronized highlighting, chapter navigation, reliable resume, and the existing installable phone experience. Build illustration coverage in reviewed batches around actual story beats. A full-audio beta can precede dense illustration coverage, provided its limited artwork is clearly described. Decide the minimum illustrated public-launch coverage before production.

### Current position

| Area | What exists | What remains |
| --- | --- | --- |
| Web reader | Live private 91-track reader; exact/canonical resume, remembered narrator, rapid narrator/chapter navigation, stable-origin controls and emulated safe-area layouts pass | Physical-phone installation/listening and later reader features |
| Audio | Both unchanged 192 kbps editions, 182 physical MP3 hashes verified, source-bound author as-is approval, measured maps with explicit gaps, and local author audit | Optional complete human listening/alignment review; no regeneration required for this private release |
| Text | Faithful canonical v6 prose, 12,922 stable sentence anchors, checked pilot migration, full reader integration and verified private export | Future manuscript migrations |
| Artwork | Painted realism; private cast bible; Adrian and Daniel model sheets | Approval of provisional models, remaining needed character/location references, chapter beat boards and final illustrations |
| Hosting | Stable private full-book release; R2/Worker gateway; all upload/final-preview HTTP/native checks and actual same-release A/B rollback/re-promotion pass; logging disabled; Git deployment guards | First-48-hour/weekly observations and funded ceiling/account alerts |
| Offline | Cached public reader shell; private API/media bypass it and audio streams online | Visited chapter text/timing cache and CDN artwork integration; offline audio is later work |

### Audio inventory verified for this roadmap

| Edition | Local production folder | Selected clips | Tracks | Runtime | Existing chapter MP3 bytes |
| --- | --- | ---: | ---: | --- | ---: |
| Female / Autonoe | Audiobook/v7 | 248 | 91 | 15:26:22 | 1,334,133,566 |
| Male / Charon | Audiobook/v8 | 248 | 91 | 15:18:56 | 1,323,420,974 |

Each edition contains opening credits/epigraph, **89 story chapters**, and closing credits. Existing chapter MP3s are mono, 44.1 kHz, 192 kbps. M4B and chapter ZIP packages also exist. The combined chapter MP3 payload is approximately 2.66 GB in decimal units.

The delivery manifests report technical completion and successful package verification. Historical production manifests retain their original human-listening flags; the later source-bound author as-is decision authorizes this release. Later analysis checked 142/496 chunks with OpenAI before credits ran out; Charon also has a completed local recheck pass with unresolved listening candidates. These results do not grant chapter approval. A single complete listening pass through both editions requires about **30 hours 45 minutes at normal speed**, before corrections and rechecks.

The inventory figures were subsequently verified against every physical chapter MP3 hash during foundation implementation. No full-book human listening pass was performed by engineering. The historical pilot edition registry remains preserved. The private release builder produces a new coherent book manifest after source and timing verification. Sources: [edition registry](../../web/data/narration-editions.json), [v7 delivery manifest](../../Audiobook/v7/delivery/delivery-manifest.json), [v8 delivery manifest](../../Audiobook/v8/delivery/delivery-manifest.json), [v7 QA status](../../Audiobook/v7/delivery/QA-STATUS.txt), [v8 QA status](../../Audiobook/v8/delivery/QA-STATUS.txt).

## 2. Decisions to settle before implementation

Record the answers in the release specification. These are planning decisions, not prerequisites for writing this roadmap.

- [x] **Publication model:** invite-only private full-book launch; see the release specification. Historical options: freely accessible, invite-only beta, or paid access? Default planning assumption: a small reviewed beta before an open launch. Public media URLs expose the files to anyone with the URL; CORS is not access control. If private or paid access is selected, add authentication, entitlements, private storage/signed delivery, and expiry handling before publishing full assets.
- [x] **Edition identity:** publish the v6 text used by both productions, unless an explicitly approved manuscript revision replaces it. Choose the public author byline, credits, narrator labels, and disclosure of generated narration/illustrations.
- [x] **Voice experience:** Female narrator / Autonoe by default; Male narrator / Charon selectable and remembered. This supersedes the initial Charon recommendation. Confirm whether new listeners choose a voice before starting and whether both must be ready for the public launch.
- [x] **Illustration coverage:** generic placeholders authorized for this private launch; character and story imagery review continues separately. Historical illustration planning: approve a baseline for every chapter and a richer beat list for priority chapters. An intentional cover treatment is acceptable for a declared audio beta; it must not silently stand in for a finished illustrated chapter.
- [x] **Continuation behavior:** implemented as an opt-in preference; returning visits remain paused. Initial planning: recommend an auto-continue preference after playback has started, plus visible Next chapter and Previous chapter controls. Returning visits open paused with Continue.
- [ ] **Hosting budget:** Cloudflare R2 and Workers Free are selected and provisioned for approximately 50 readers; Rob Xie owns operations. Record the monthly spending ceiling and account alerts/usage-limit response. Include artwork revisions and hosting separately from earlier TTS generation budgets; no paid Workers upgrade is authorized.
- [x] **Public address:** preserve the current origin for existing bookmarks. If a custom domain is desired, plan an explicit bookmark export/import or migration flow before switching; a redirect alone cannot transfer browser storage.
- [x] **Download products:** M4B/ZIP download products remain later work. Initial planning: decide whether to offer the existing M4B/ZIP packages at launch. Download buttons are separate from offline playback inside the app.

## 3. Target architecture

~~~mermaid
flowchart LR
    T[Approved canonical manuscript] --> B[Offline release builder]
    A[Approved audio for each voice] --> B
    I[Approved models and paintings] --> B
    B --> D[Versioned chapter text and timing data]
    B --> M[Immutable audio and artwork on media CDN]
    D --> V[Vercel app and book manifest]
    V --> R[Book controller: chapter, voice, resume]
    R --> P[Native HTML audio and Media Session]
    M --> P
    M --> S[Current and next scene]
    R --> L[Local bookmarks and preferences]
~~~

Retain a small static application for the first launch. Accounts, billing, and cloud bookmark sync are optional additional services unless the publication model requires them. HLS/adaptive streaming can be reconsidered if measured playback or traffic justifies it; chapter MP3s with working byte-range delivery are the initial approach.

### Publication contract

| Shared between voices | Specific to each recording |
| --- | --- |
| Book/manuscript/release IDs; ordered parts and tracks; canonical chapter text | Audio URL, codec, byte count, SHA-256, decoded duration |
| Stable chapter-qualified paragraph and sentence anchors | Sentence start/end times, intro/credit cues, mastering/encoding clock offsets |
| Scene ID, canonical trigger, approved artwork, spoiler-safe caption/alt text | Scene timestamps resolved from that voice's sentence map |
| Character/location references and production reveal rules | Content approval, alignment approval, readiness and any unavailable track state |

Create a lightweight book manifest with 91 ordered tracks and a story-chapter table of contents with 89 entries. Distinguish opening credits, story chapters, and closing credits. Include per-voice availability and all manifest/data hashes needed to validate a coherent release.

Proposed layout; paths and filenames are implementation targets, not existing exports:

~~~text
web/dist/
  data/book.json
  data/releases/<release-id>/tracks/<track-id>/text.<hash>.json
  data/releases/<release-id>/tracks/<track-id>/scenes.<hash>.json
  data/releases/<release-id>/tracks/<track-id>/<voice>/timings.<hash>.json

media storage/
  lumen/<release-id>/<voice>/<track-id>/audio.<sha256>.mp3
  lumen/art/<scene-id>/<art-version>/phone.<hash>.webp
  lumen/art/<scene-id>/<art-version>/large.<hash>.webp
~~~

For a public release, keep chapter text and timing JSON on the app origin initially to simplify caching, and store bulk audio and artwork on the chosen media host. The proposed static layout assumes this public model. For a private or paid beta, keep protected exports outside public web/dist and fetch authorized text, timings, artwork, and audio through the selected access architecture. Keep full cast/model sheets, generation prompts, internal aliases, raw recordings, receipts, and lossless masters outside the public runtime.

## 4. Ordered delivery plan

### P0 — Freeze scope and create a release inventory

Owner: author/product, with engineering and audio production.

- [ ] Complete the decisions above and assign a release ID and owners.
- [x] Inventory all 91 tracks × two voices: source text hash, selected clip IDs, master path, final web audio hash, duration, size, technical warnings, content approval, timing approval, artwork status.
- [x] Reconcile track order, part headings, spoken chapter headings, credits, epigraph, and chapter labels. All 91 selected track/source-text extents are present; spoken content follows the recorded author as-is approval.
- [x] Preserve the existing pilot and source production directories. Use a separate release export/staging location; do not overwrite v7/v8 assets in place.
- [ ] Complete cloud backup verification of approved source text, masters, manifests, and art references. The versioned ZIP is present in private Lumen Drive with matching size; full local verification and a seven-file local restore passed. Cloud checksum/remote restore proof is still pending. Keep bulk production audio and API receipts outside Git.
- [x] Define publication readiness separately from generation/mastering/package completion.

**Exit gate:** one reconciled inventory, agreed launch scope, and identified reviewers. The inventory initially shows pending approvals rather than pretending the recordings are ready to publish.

### P1 — Finish narration content QA and lock the final recordings

Owner: audio production and author/editor.

- [x] Restore a working transcription environment. Subsequent production runs completed Charon local rechecks and used OpenAI gpt-4o-transcribe for 142 chunks, retaining model/cost/provenance. The OpenAI collection remains incomplete because API credits are exhausted.
- [x] Compare every track against the canonical narration text using local source-bound ASR; the author accepts current narration as-is. Automated comparison does not claim human listening approval. Flag omissions, additions, repetitions, wrong names, accidental instructions, mispronunciations, abrupt joins, and unusual silence. ASR discrepancies are review candidates, not automatic proof of a narration defect.
- [ ] Listen to both complete editions and every flagged passage, including track starts/ends and every join between the 248 selected clips in each edition. Record approved, accepted-with-note, or correction-needed for each finding.
- [ ] Review the warning lists in section 9. Specifically compare opening credits: the same 44-word source runs about 98.99 seconds in Autonoe and 21.78 seconds in Charon. This is an observed discrepancy to inspect, not a diagnosed defect.
- [x] No repairs for this release: the author accepts the existing narration as-is and explicitly requested no regeneration. Preserve every original master and its source lineage.
- [ ] Recheck duration, sample continuity, mono format, peak/RMS measurements, joins, starts/ends, and consistent loudness. Apply the chosen web quality standard; separate platform-specific download/submission requirements.
- [x] Record the explicit author as-is content decision bound to all **182 edition-track combinations**; preserve the historical production/listening flags.
- [x] Freeze approved masters with checksums, provenance, and an auditable approval record.

**Exit gate:** both editions are content-approved, warnings are dispositioned, and the chosen master set is immutable. Full-book illustration generation remains on hold until this gate, consistent with the existing project direction. Storyboards, inventories, and reader infrastructure can be prepared earlier.

### P2 — Build canonical text and a repeatable web export pipeline

Owner: engineering, reviewed by author/audio editor.

- [x] Preserve the existing pilot exporter. Add a full-book exporter that accepts a track ID, narrator ID, release ID, and selected approved source files. The current prepare_pilot.py is intentionally hardcoded to the v6 Chapter 1 excerpt and a roughly five-minute cut.
- [x] Build a canonical anchor registry. Current pilot IDs such as p001-s01 are positional and chapter-local; qualify them by chapter and keep an explicit alias/migration map for the existing pilot. New manuscript revisions must not silently reuse an ID for different prose.
- [x] Preserve punctuation, paragraph boundaries, emphasis, headings, and credits. Keep normalization used for ASR comparison separate from the displayed manuscript.
- [x] Use the existing 192 kbps MP3 chapters, as explicitly selected by the author; no re-encoding. Representative native playback/seek checks pass. Retain lossless masters.
- [x] Bind each timing map to the **final encoded web file**, including its SHA-256 and decoded media duration. Re-encoding changes the recording identity and can change clock offsets.
- [x] Align every final track independently for both voices; retain 2,171 explicit unavailable positions without invented cues. Account for spoken introductions, mastering edits, pauses, joins, and encoder delay; do not copy v6 pilot timings or one narrator's seconds into another recording.
- [ ] Map approved scene triggers to canonical sentence anchors, then resolve their timestamps against each narrator's map.
- [x] Add idempotent, restartable processing: unchanged hashes skip completed work; retries and interrupted runs never duplicate or silently replace approved exports.
- [x] Validate all text reconstruction, unique anchors, track order, monotonic/bounded measured cues, intro/tail coverage, scene references, durations, file hashes, and readiness flags. Required recordings must verify; schema-2 explicitly unavailable cues retain faithful prose and cannot seek/highlight/reveal.
- [ ] Review cue accuracy with final audio, especially chapter boundaries, paragraph starts, rapid exchanges, names, and every illustration reveal. Proposed acceptance target: sentence onset within ±0.5 seconds on reviewed anchors, with every reveal cue reviewed; refine the target during beta.
- [x] Emit a versioned manifest, per-track data, release report, and upload inventory. Never mark a track ready from the existence of an MP3 alone.

**Exit gate:** reproducible exports for all tracks and both voices, validated against final audio and approved prose. A change to an audio hash invalidates its old timing map.

### P3 — Provision media delivery and prove browser streaming

Owner: engineering/operations, budget approved by product.

**Selected delivery:** private R2 Standard storage with a Workers Free gateway, retaining the existing Vercel reader. R2 public domains remain disabled. The provider comparison below is historical planning; direct R2 egress is free while storage, operations and attached services retain their own allowances and limits.

| Option | Useful fit | Decision and verification |
| --- | --- | --- |
| Vercel public Blob | Simple public-media integration with the existing Vercel project | Check account limits and metered delivery costs; full chapter MP3s already total more than the current 1 GB Hobby storage allowance. Public blobs are URL-accessible and CDN-delivered. [Public storage](https://vercel.com/docs/vercel-blob/public-storage), [pricing/limits](https://vercel.com/docs/vercel-blob/usage-and-pricing) |
| Cloudflare R2 Standard + custom domain | Public audio/art delivery with no direct R2 egress charge | Confirm account/domain setup, storage/operation costs, caching rules, and browser headers. Use a production custom domain; r2.dev is intended for development. Other attached metered services can still charge. [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/) |
| Private/paid delivery | Invite-only or purchased access | Protect text, timing data, artwork, and audio. Implement private objects/authorized delivery, entitlement checks, URL refresh, and byte-range playback tests; prove the chosen provider's approach before selection. An obscure public URL or CORS policy does not restrict who can download a book. |

- [x] Create the private store/bucket and scoped uploader outside browser code/Git. R2 public domains are disabled; encrypted Worker and preview bridge settings are configured.
- [x] Configure the three production bridge settings, verify final-source preview delivery and promote the stable private release; retain previous identities and the matching same-release rollback configuration.
- [x] Add invite-only authorized manifest/data delivery; fixtures verify expired/revoked access, logout and every protected asset type. Final-source hosted HTTP checks cover all 365 immutable assets; expired/revoked cases also have separate fixture evidence. Session-bound grants permit new Range requests within their current expiry; include authorization traffic in the capacity model.
- [x] Upload the approved private release under immutable content-hashed keys: 182 original audio/mpeg MP3s and 183 JSON files. No story artwork was added; original audio bytes are preserved.
- [x] Apply private/no-store to protected JSON and media, and keep private requests out of the service worker. Immutable R2 keys and a hash-bound index are retained for rollback. Public caching and M4B/ZIP downloads remain separate future choices.
- [x] Configure private Worker CORS for the grant’s exact reader origin, including the canonical app and explicitly allowed preview. Live gateway checks passed canonical/opaque-origin CORS, authenticated preflight, HEAD, exposed range/length/ETag headers and invalid/foreign-origin denials. Final Vercel preview and stable-origin Chrome acceptance pass; physical devices remain separate. [Worker delivery](cloudflare-delivery.md)
- [x] Verify live Cloudflare GET ranges return **206**, exact bytes, correct Content-Range and unchanged total size, including beginning/middle/end, closed/open/suffix ranges, conditionals and unsatisfiable ranges. Direct native Chrome also sought unbuffered audio in both voices. Final Vercel app-control/browser acceptance passes on the preview and stable origin.
- [x] Complete final native cache/wire/failure verification with the raw Chrome protocol observer, no request interception and normal Chrome transport. Final-source HTTP checks verify all 1,095 sample ranges and the full largest MP3 hash; native offline failures are retained under explicit bounded simulation exclusions. [Transport proof and limits](cloudflare-delivery.md).
- [x] Verify all 365 uploaded object SHA-256 values through complete remote reads against the release inventory; also verify all 1,095 sample ranges and final object identities. R2 ETags are recorded only as validators.
- [x] Finish installed-Chrome browser/native checks on the protected preview and ordinary stable-origin Chrome checks. Physical Android installation/listening acceptance remains a separate follow-up.
- [x] Publish the immutable server index last, after every required object passed integrity verification. This completes storage publication, not production reader promotion; prior assets remain available for rollback.

**Exit gate:** verified public or authorized delivery, native seeking on the chosen host, a funded operating plan, and bulk media archives outside web/dist. Prefer direct CDN media delivery; any provider-required authorized streaming route must preserve ranges and have measured transfer/function costs.

### P4 — Turn the pilot into a full-book reader

Owner: engineering/product.

- [x] Add a book controller that reads saved progress **before choosing a chapter**, fetches the book manifest, then loads the saved chapter and ready narrator. The current code loads one global LUMEN_CHAPTER and would ignore a bookmark for another chapter.
- [x] Refactor chapter loading into cancellable transitions. Save outgoing progress, stop stale timers/crossfades, load text/timings, set the audio source, resolve the bookmark, and render the correct scene. Ignore late responses from an earlier chapter or voice request.
- [x] Reuse one native audio element and the existing seek queue/error recovery. Do not replace streaming with a large full-book audio fetch.
- [x] Add a part/chapter drawer, current chapter label, previous/next controls, deep links, browser Back behavior, loading/retry states, and book completion. Distinguish chapter progress from whole-book progress and playback position from manually read-ahead text.
- [x] Replace Chapter 1/pilot-specific labels, end/replay behavior, descriptions, and hardcoded scene alt text with manifest-driven metadata.
- [x] Add the narrator picker only for approved, fully exported editions. Switching voice maps the current canonical sentence and fraction to the other timing map, retains speed/text/art preferences, and preserves whether the listener was paused or playing.
- [x] Handle a temporarily unavailable chapter or voice without silently starting another passage. Keep the old chapter usable on a failed transition; offer an explicit retry or available-voice choice.
- [x] Add opt-in automatic chapter continuation. Advance exactly once, save completion first, respect browser playback policy, and show a Continue/Play action if automatic playback is blocked.
- [x] Preserve the last book position and add separate per-chapter history/completion. Keep the current v1 bookmark and legacy migration until a verified migration has succeeded; never delete a usable old bookmark first.
- [x] Retain exact seconds for the same recording hash. For a changed recording or voice, use semantic anchors. A completed five-minute pilot must continue at its passage in the full chapter, not mark the whole chapter complete or return to zero.
- [x] Define recovery for missing manuscript anchors, storage denial/corruption, two tabs, and overlapping saves. A later cloud-sync project must resolve conflicts by intentional updates, not simply choose the furthest time.
- [x] Update Media Session title/artwork/duration/position per track; add supported previous/next-track actions to the chapter queue. [Chrome Media Session](https://developer.chrome.com/blog/media-session)

**Exit gate:** a two-voice, three-chapter test slice works end to end, including pilot migration, then the same controller loads the full 91-track inventory without special-case code.

### P5 — Produce a consistent illustrated book

Owner: author/art direction and image production; starts after P1 approval.

- [ ] Approve or revise the provisional Adrian and Daniel sheets. The current private cast registry contains 19 principal and four supporting records; only those two model sheets exist.
- [ ] Prioritize model sheets for characters appearing in the next approved chapters. Create front/profile/three-quarter/full-body views, expression and wardrobe variants, and necessary age/timeline variants. Do not infer appearance from names or roles.
- [ ] Build location references and recurring props before repeated scenes. Record model versions, references, wardrobe, setting, palette, camera language, and intentional changes.
- [ ] Create chapter beat boards tied to canonical sentences: establishing scenes, punchlines, emotional turns, discoveries, location changes, and key actions. Avoid repeating an apartment shot through distinct story beats.
- [ ] Use the approved cinematic painted realism, charcoal/ivory/gold design, and shared reference pixels in generation calls. A character name in a prompt alone does not preserve a face.
- [ ] Generate a representative three-chapter batch, review it in actual narration, and estimate the remaining scene count and revision effort before scaling.
- [ ] Approve each painting for identity, clothing, geography, chronology, composition, and reveal timing. Keep internal identity/model sheets out of runtime data; use spoiler-safe captions, alt text, thumbnails, and asset names.
- [ ] Store the scene's canonical trigger separately from recording-specific timestamps. Both voices reuse the same painting and reveal meaning.
- [ ] Export phone and larger WebP variants with explicit dimensions; preserve original art sources and provenance. Aim for approximately 100–300 KB per phone painting where visual quality allows, measured rather than assumed.
- [ ] Keep current-plus-next loading, low-priority next-image prefetch, and Save-Data behavior. Use a scene loading/error fallback that does not interrupt narration or misrepresent a future scene.
- [ ] Review the cadence while listening: enough images for the punchlines and story movement, with pauses long enough to absorb each painting. Avoid arbitrary page-turn timing.
- [ ] Complete the agreed baseline coverage for all story chapters. Mark rich-illustration expansion separately so a full-audio beta and a finished illustrated release have honest status.

**Exit gate:** approved baseline coverage, stable character/location appearances, reviewed reveal cues in both voices, and a bounded asset budget. References: [cast bible](../../web/art-direction/cast-bible.json), [continuity rules](../../web/art-direction/README.md).

### P6 — Extend phone, caching, accessibility, and resilience

Owner: engineering and QA.

- [x] Keep the portrait timeline across the screen, controls at least 44 px, landscape layout, notch/home-indicator spacing, adjustable type, and persistent Art mode.
- [x] Make the chapter drawer and narrator picker usable at narrow widths; automated phone layouts verify usable controls and unobscured transport.
- [ ] Later: add protected visited chapter text/timing and artwork caching with explicit entitlement and storage budgets; the launch caches only the small shell. Do not precache all 182 recordings or all book illustrations.
- [x] Cache the private reader shell only by default; protected prose, timing, artwork and audio bypass the worker. This is the selected private launch policy. Enable protected chapter/art caching only after defining offline entitlement, logout/expiry behavior, and removal of protected cache entries. Do not cache signed URLs beyond the intended access policy or promise remote revocation of files already downloaded to a device.
- [ ] Later, with approved artwork and offline entitlements: adapt the worker's same-origin artwork cache for the selected media CDN using an origin allowlist and complete CORS-readable image responses. The current worker ignores external hosts and only knows Chapter 1 data.
- [x] Keep **all audio and Range requests outside service-worker interception** for the initial release. Explain that cached reading may work offline while narration requires a connection.
- [x] Version shell caches and immutable book data coherently; keep listening tabs on their active release, retain their referenced assets, and activate updates after they close. Do not force reload or switch an active recording mid-playback.
- [ ] Handle airplane mode, lost signal, stale manifests, a missing painting, quota limits, and retry at the saved place. Browser storage is evictable; cached content is not a permanent offline guarantee. [Browser storage](https://web.dev/articles/storage-for-the-web)
- [x] Use semantic prose and a bounded keyboard Tab chain with roving passage focus and keyboard seeking. Automated accessibility checks pass; physical TalkBack acceptance remains in P7.
- [ ] Test contrast, focus, screen-reader labels/live announcements, reduced motion, large text, text-only reading, and controls with artwork hidden.
- [ ] Set and measure beta performance budgets: small book index; only current chapter text/cues and current audio metadata at startup; current/next artwork only. Proposed targets: first readable text and playback start within 3 seconds at p95 on a documented 5 Mbps/100 ms network profile, and no sustained rebuffering on supported connections. Record cold/warm results separately.

**Exit gate:** responsive and accessible full-book playback with bounded initial downloads, recoverable failures, honest offline behavior, and an update path that preserves a listening session.

### P7 — Validate content, devices, and a controlled beta

Owner: QA, author/audio editor, beta listeners.

- [x] Extend existing progress and browser tests for multiple chapters, credits, both voices, changed recordings, pilot-to-full migration, completed chapters/book, deep links, missing anchors, rapid chapter/voice switches, and stale async responses.
- [x] Run automated integrity checks for all 182 track/voice exports; scene cues are empty for the authorized placeholder release. Confirm each text anchor is unique within its namespace and every referenced media/data file exists.
- [x] Verify all 182 source-bound author as-is narration approvals; use the expressly authorized generic placeholders. Illustration/reveal approvals remain held with artwork. Automated script fixtures and package checks do not replace listening or art review.
- [x] Test representative real HTTPS/CDN loading, playback/seek/resume, both voices and connection loss/recovery. Normal Chrome has no cache/network overrides; the separate native gate uses documented CDP throttling/offline/cache isolation. Expiry/revocation and blocked continuation retain separate fixture evidence; no exhaustive device/session coverage is claimed.
- [ ] On an **actual Android Chrome phone**, install Lumen, launch its icon, lock the screen, leave/reopen the app, cross chapter boundaries, switch voices, use Bluetooth/headset/media buttons, interrupt playback with a call, and test network loss/reconnection. Current automated phone checks do not establish these physical behaviors.
- [ ] Test portrait/landscape, small screens, maximum text size, hidden artwork, TalkBack, browser Back, and a long listening session. Run a secondary iPhone/browser pass if that platform is promised at launch.
- [ ] Test full-book completion and return after hours/days. Explicitly verify a returning listener is paused at the correct chapter/passage rather than silently restarting.
- [x] Select a private shared-code launch for approximately 50 readers, superseding the proposed 10–25 reader beta. Actual invitation distribution follows verified deployment; both voices are included.
- [ ] Use private hosting request/error and transfer/spend logs for the launch. New product analytics remain later. Monitor request authorization, failed audio/data requests, missing assets and transfer/spend; use reader feedback for playback and resume failures. Avoid collecting raw manuscript text or complete local bookmarks.
- [ ] Collect feedback on narration, image cadence, comfort, resume, installation, and controls; fix blocking issues and repeat only affected checks.

**Exit gate:** no unresolved critical content/playback/resume defect, complete approval inventory, passed targeted/full regression checks, physical Android acceptance, and beta evidence supporting launch. Beta size and performance targets are proposed planning values.

### P8 — Publish the full-book release and establish operations

Owner: product/author and engineering/operations.

- [x] Finalize the authorized private-release metadata: generic placeholders, “Inspired by Rob Xie”, Female/Male labels, AI narration disclosure, shared access code and support through the person who shared it. Cover/story artwork, public pricing and optional analytics/accounts remain later.
- [x] Retain the release checklist, approved immutable manifest, reviewed source, migration limits, previous identities and actual same-release rollback record.
- [x] Upload and verify immutable media first, then test the exact final-source preview and stable app. Saved nonfirst places in both voices survive the actual same-release A/B rollback/re-promotion; pilot migration retains separate local evidence.
- [x] Verify all 91 tracks per voice, canonical prose and immutable hashes. No story scenes are required for the authorized placeholder release; credentials and private production/model/audit data stay excluded.
- [x] Deploy from `web/` to the existing Vercel project and explicitly promote the reviewed deployment. The command below stages the production artifact without assigning its domain. Promote the exact reviewed deployment ID explicitly, then verify the production alias and postflight separately:
  ~~~sh
  vercel deploy --prod --yes --skip-domain --scope robert-xies-projects
  ~~~
- [ ] Physical Android installation/lock-screen/headset and long-session/full-book completion acceptance remain open. Production alias/auth/first-last tracks, representative native playback/seek/narrator/bookmarks and emulated layouts pass; the latter do not complete these physical or exhaustive listening checks.
- [x] Save deployment/source/hash/verification receipts privately and commit reviewed reader/export metadata. Operational documentation is reviewed separately; bulk media remains outside Git. Drive test-apps remains for Nimblip only.
- [x] Promote the full-book app and unchanged manifest/index coherently. Immutable audio is retained, with no byte mutation behind existing URLs and no deletion of referenced older assets.
- [ ] Follow the first-48-hour owner, feedback, incident and spending thresholds in [private-launch.md](private-launch.md). Configure funded account alerts and record actual checks after deployment.
- [x] Exercise operational rollback and re-promotion using compatible same-release A/B deployments and retained semantic progress. A future content/code regression needs its own compatible fallback and affected-asset verification; prior-pilot or cross-release compatibility is not proven.
- [ ] Review playback, completion, costs, and feedback weekly during the initial release period; prioritize evidence-driven improvements.

**Exit gate:** the full audiobook is available from the stable web app, both promised voices work, old listeners resume correctly, real-phone installation/playback passes, and operations/rollback have been exercised.

## 5. Capacity and cost planning

Model **actual delivered bytes**, including partial listens, repeated listens, seeks, both-voice auditions, artwork, and optional package downloads. A listener usually completes one edition; do not automatically bill the model as if everyone downloads both.

| Scenario | Approximate audio transfer using existing chapter MP3s |
| --- | ---: |
| One complete edition | 1.32–1.33 GB |
| 100 full-book completion equivalents/month | 132–133 GB |
| 1,000 completion equivalents/month | 1.32–1.33 TB |
| 10,000 completion equivalents/month | 13.2–13.3 TB |

These are derived estimates from current local file sizes, excluding images, retries, and downloads. The discarded 128 kbps option at the current durations would have been roughly 0.88–0.89 GB per edition before container overhead; quality and actual output size must be measured.

- [ ] Build a cost sheet for storage × retained versions, audio/image bytes delivered, CDN/object requests, cache misses, optional backend/signed-URL traffic, domain, and monitoring.
- [ ] Estimate artwork count from approved beat boards; model generation attempts, rejected variants, model-sheet work, optimization, and human review rather than charging only final paintings.
- [ ] Set a monthly limit and actionable alerts; confirm the host's behavior at allowance/spend limits and a response that preserves existing listeners.
- [ ] Measure a complete and a partial beta listen on the actual host; compare metered usage with estimates.
- [ ] Keep app generation/TTS out of user playback requests. CDN media delivery should scale independently from app deployments or a database.
- [x] Review current provider prices and account entitlements at provisioning time. R2 Standard includes account-wide storage/operation allowances and no direct Internet egress fee; Workers remains Free. The 2.6676 GB release fits otherwise-unused storage allowances. Overage billing, request/CPU limits, existing Vercel charges and an author-set spending ceiling remain separate. Provider cost is not the total operating cost. [Vercel usage](https://vercel.com/docs/vercel-blob/usage-and-pricing), [R2 usage](https://developers.cloudflare.com/r2/pricing/)

## 6. Work order and first implementation slice

Critical path: **publication decisions → audio content approval → final web encodes → per-voice alignment → verified delivery and book controller → private placeholder release verification → stable-origin launch → physical-phone follow-up**.

Engineering can design contracts, navigation, fixtures, and hosting tests while audio review runs. Artwork production depends on the approved final book/audio and approved character references. Do not estimate a public launch date before the content-review and image-coverage inventory is complete.

| Priority | Concrete backlog item | Depends on | Done when |
| --- | --- | --- | --- |
| P0 | Reconcile 91-track/two-voice inventory and publication scope | Author decisions | Readiness fields and owners are explicit |
| P0 | Fix transcription environment; complete both audio reviews | Inventory | 182 content approvals; warnings resolved/dispositioned |
| P0 | Canonical anchors + full-book export/validation tool | Scope; final encodes for final maps | Repeatable text/audio/timing manifests |
| P0 | Media host proof with one chapter in both voices | Access/budget decision | Correct CORS, exact 206 bytes, native phone seeking |
| P0 | Three-chapter/two-voice reader slice | Contract and validated sample exports | Navigation, semantic switching, resume and end behavior |
| P0 | Full inventory integration and visited-content cache | Proven reader slice | All 91 tracks per voice load without special cases |
| P1 | Character/location approval and first art batch | Approved audio; model review | Consistent identities and reviewed beat cadence |
| P1 | Agreed chapter illustration coverage | First batch | Coverage/reveal gates complete |
| P0 | Physical Android acceptance + beta | Complete release candidate | Real install/lock-screen/transition/resume evidence |
| P0 | Public release + rollback/monitoring | All launch gates | Verified stable-origin deployment and assigned operations |
| P2 | Offline audio, cross-device sync, richer art, optional commercial features | Launch evidence and access-model scope | Separately designed and tested releases |

**First build after planning:** use three representative story chapters in both voices, plus credits and a migrated pilot bookmark. Prove the release contract, media host, chapter transitions, narrator mapping, and phone resume before multiplying production across the book. Then export the full inventory through the same pipeline.

## 7. Later releases

### Explicit offline audiobook downloads

- Let readers download individual chapters/editions intentionally, with size/progress/cancel/delete controls.
- Use persistent metadata, storage estimates, interrupted-download recovery, checksum verification, and an eviction-aware UI.
- Design and test offline byte-range serving as a separate feature before changing the current audio-bypass worker policy.
- Handle voice-specific downloads and release revisions without deleting the only working copy unexpectedly. Downloading an M4B/ZIP to the phone is a separate product option.

### Cross-device progress

- Add optional accounts and a small progress service; keep anonymous local reading functional.
- Sync semantic position, narrator preferences, chapter completion, and explicit update time.
- Define conflict handling for offline devices, two tabs, intentional rewind, revisions, account logout, and deletion. Avoid treating the furthest position as always correct.

### Continued experience improvements

- Add a sleep timer, useful personal bookmarks, search/TOC improvements, and richer approved paintings based on beta feedback.
- Consider HLS or an alternative speech codec only after delivery metrics demonstrate a benefit and seeking/alignment remain correct.
- If paid access is chosen later, implement entitlement-aware delivery for text/timings as well as audio; previously public files cannot be made secret by hiding a button.

## 8. Private-launch acceptance checklist

- [x] Delivered access, credits, female default, remembered narrator and generic illustration promises match the authorized private scope.
- [x] Both editions have all 91 ordered tracks, source-bound author as-is approval and immutable hashes; no full human listening claim.
- [x] Approved manuscript prose, canonical anchors and local pilot migration checks pass.
- [x] Each recording has its own technical hash-bound measured/null timing map. Story reveal cues are held with artwork.
- [x] Final-preview and stable-origin native playback, Range seeking, chapter navigation, narrator changes and representative resume pass.
- [x] Saved nonfirst positions in both voices survive the actual stable-origin same-release rollback/re-promotion.
- [ ] Future story artwork, multi-view character models, captions and spoiler-safe reveal coverage require separate review.
- [ ] Physical Android installation, TalkBack, lock-screen/headset/call interruption and long-listening acceptance remain open. Automated emulated phone/iPad/desktop safe-area checks pass.
- [x] Initial loads/caches remain bounded; private API/media bypass the service worker and narration needs a connection.
- [ ] Full cloud-backup ZIP hashing/remote restore and funded spending alerts remain open. Local backup verification/restore, publication/media verification, support ownership and same-release operational rollback are complete.
- [x] Production uses the existing address. Same-release bookmark continuity is exercised; pilot migration/backward-release compatibility remains separate local or unperformed evidence.
- [x] Release records distinguish technical checks, author content acceptance, emulated layouts and unperformed physical-device/human-listening tests.

## 9. Current narration review hotspots

These lists are review inputs from existing mastering reports, not confirmed narration defects.

- **Autonoe:** chapters 001, 014, 018, 026, 037, 048, 052, 066, 081, 082. Most warnings concern resampling interpolation. Chapter 081 is 0.06 dB outside the RMS tolerance; chapter 082 has a retained 2.09-second quiet region at 238.97 seconds.
- **Charon:** chapters 004, 005, 008, 013, 014, 027, 028, 031, 052, 057, 066, 069, 075, 080, 086 have RMS/peak-budget warnings. Chapters 005, 013, 031, 066, 086 also have interpolation warnings.
- **Opening credits:** compare the 98.99-second Autonoe and 21.78-second Charon versions of the same 44-word source.
- **Both editions:** current audio is now accepted as-is by the author. Historical comparison/listening flags remain unchanged. Technical package and synthetic helper tests do not certify spoken prose.

Consult [v7 mastering report](../../Audiobook/v7/mastered/mastering-report.json) and [v8 mastering report](../../Audiobook/v8/mastered/mastering-report.json) for the actual measurements and later dispositions.

## 10. Implementation references

- [Private release specification](release-specification.md)
- [Audio preparation](audio-release.md), [release exporter](release-export.md), [backup recovery](backup-recovery.md) and [archive backup](archive-backup.md)
- [Current reader and deployment instructions](../../web/README.md)
- [Book/edition plan](../../web/data/narration-editions.json)
- [Pilot text/audio/timing contract](../../web/data/chapter-001.json)
- [Current pilot exporter](../../web/scripts/prepare_pilot.py)
- [Reader controller](../../web/dist/app.js) and [progress persistence](../../web/dist/progress.js)
- [Service worker](../../web/dist/sw.js) and [PWA manifest](../../web/dist/manifest.webmanifest)
- [Character continuity rules](../../web/art-direction/README.md) and [cast registry](../../web/art-direction/cast-bible.json)
- [Audio production v7](../../Audiobook/v7/delivery/README.txt) and [v8](../../Audiobook/v8/delivery/README.txt)
- [Progress tests](../../web/tests/progress.cjs), [reader checks](../../web/tests/browser-smoke.cjs), [resume checks](../../web/tests/resume-navigation.cjs), [phone/PWA checks](../../web/tests/mobile-pwa.cjs)
- [Latest deployment evidence](../../web/data/deployment.json)

External hosting/browser documentation and account settings were checked on 10–11 October 2026. The initial private full-book launch used the existing address and reviewed source `48f4b56865b1f6e472aa69bf171a4b346e9d0516` / identical merge `363c67e5b02454c7b28bfc4013a7f10bc0e51fff`. [Cloudflare delivery](cloudflare-delivery.md) records final-source preview HTTP/native/browser and stable-origin same-release rollback/Chrome evidence with their limits. Physical Android, later artwork, first-48-hour operating observations, funded alerts and full cloud-backup byte verification remain open. The whole roadmap and full human listening are not marked complete.
