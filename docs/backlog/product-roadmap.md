# Product roadmap: full Lumen audiobook web launch

Status: private full-book implementation and verification; hosting promotion and backup verification pending. Updated: 10 October 2026. Primary device: Android with Chrome.

### Execution record — 10 October 2026

The author selected **local-only access for the narration audit** and requested character confirmation before further art production. Unfinished audio, manuscript exports, and author notes remain local.

- **Delivered: author audit.** [PR #1](https://github.com/mildmanneredme/lumen/pull/1) merged after two independent review rounds and fixes for stale review bindings and generation-input verification. Run `python3 web/scripts/audit_server.py` and open `http://127.0.0.1:8766/audit`. The queue contains 377 OpenAI candidates plus 755 unresolved/inconclusive local Charon candidates, with distinct evidence and audio clocks. Feedback persists with revision history; export groups explicitly requested corrections into source-take repair plans. Seventeen backend and eighteen Chrome behavioral checks pass. [Audit guide](../../web/audit/README.md).
- **Delivered: private P0/P2 foundation.** Reconciled 91 tracks, 89 story chapters, 12,922 chapter-qualified sentence anchors, 182 narrator recordings, and 60 pilot aliases. All 182 existing MP3 hashes matched their delivery manifests (2,657,554,540 bytes); all 496 selected raw WAV hashes also matched their master inputs (5,292,198,464 bytes). Timing validation binds the final audio, canonical text, and reviewed map hash; exports are immutable and reject symlink escapes into public assets. Faithful retakes retain canonical anchors, while selected-take and mastering identities must agree. Publication also requires physical raw-source verification bound to the selected take set. Fifty-five foundation tests passed at the reviewed PR #2 merge. [Content contract](content-contract.md).
- **Ready for author confirmation:** [23 character profiles and two provisional model sheets](character-confirmation.md). Seb's height discrepancy, Polk's age, and Daniel's wardrobe are explicit decisions. No model or appearance has been marked approved.
- **Author release decision:** current audio accepted as-is; no regeneration or further paid transcription required. Use all existing 192 kbps MP3s, female voice by default, generic illustration placeholders, and “Inspired by Rob Xie”. The private audience is approximately 50 readers. [Release specification](release-specification.md).
- **Access decision:** one memorable shared code for the private audience, with returning device sessions and revocation/rotation. The code stays in encrypted operator records; only its hash enters server configuration.
- **Prepared and independently reviewed:** invitation sessions/private Blob delivery ([PR #3](https://github.com/mildmanneredme/lumen/pull/3)), full-book navigation/resume/narrator switching ([PR #4](https://github.com/mildmanneredme/lumen/pull/4)), and source-bound technical timing/export/backup tools. 133 Chrome behavioral checks, 73 controller tests, 49 private-backend/operator and deployment checks, and the cache regression pass. The release pipeline has 324 passing release Python fixtures; 40 further actual-release Chrome checks validate all 182 payloads, canonical prose and ten representative MP3s. Physical phone acceptance and actual hosted-media verification are separate checks.
- **Timing evidence:** all 496 selected raw clips have local ASR caches. Across both recordings, 23,673 sentence positions have measured cues and 2,171 are explicitly unavailable. Uncertain passages retain their exact manuscript text and audio; they receive no invented seek/highlight time. Technical alignment verification does not claim a full human timing review.
- **Local export verified:** 365 immutable assets / 2,667,475,488 bytes prepared after fresh verification of actual masters, selected raw sources and exact ASR caches; all 1,095 inventory range samples match source bytes. No remote publication is implied.
- **Remaining release work:** private Blob provisioning/upload and hosted range verification, stable-origin deployment, private Drive backup/remote checksum/restore proof, hosting budget and repository privacy decision. [Private release operations](private-launch.md) assigns support, incident thresholds and coherent rollback evidence. Character/model and story artwork review remain separate holds.

The author has authorized a full private launch with placeholders after technical verification. Earlier public-beta/artwork and complete-listening gates below are superseded where they conflict with the recorded release specification. Unperformed checks remain open; the production pilot is unchanged until verified promotion.

## 1. Launch approach

Keep the existing Vercel reader and stable production address, [lumen-phi-five.vercel.app](https://lumen-phi-five.vercel.app/). Turn the single-chapter pilot into a book reader that loads one chapter and one narrator at a time. Publish approved chapter audio and artwork through object storage with CDN delivery. Generate all narration, timing maps, and artwork during production; the listener's browser plays prepared assets.

The first full-book release should provide both approved voices, faithful prose, synchronized highlighting, chapter navigation, reliable resume, and the existing installable phone experience. Build illustration coverage in reviewed batches around actual story beats. A full-audio beta can precede dense illustration coverage, provided its limited artwork is clearly described. Decide the minimum illustrated public-launch coverage before production.

### Current position

| Area | What exists | What remains |
| --- | --- | --- |
| Web reader | Local private full-book reader: 91 tracks, remembered narrator, canonical/exact resume, chapter navigation and completion; native touch/mouse seeking and responsive phone/iPad/desktop controls pass | Verified hosted private delivery and real-phone installation acceptance; live pilot remains in production until promotion |
| Audio | Both unchanged 192 kbps editions, 182 physical MP3 hashes verified, source-bound author as-is approval, measured maps with explicit gaps, and local author audit | Optional complete human listening/alignment review; no regeneration required for this private release |
| Text | Faithful canonical v6 prose, 12,922 stable sentence anchors, checked pilot migration, full reader integration and verified private export | Future manuscript migrations |
| Artwork | Painted realism; private cast bible; Adrian and Daniel model sheets | Approval of provisional models, remaining needed character/location references, chapter beat boards and final illustrations |
| Hosting | Stable Vercel pilot; reviewed invitation/session/private Blob API and root/web Git deployment guards | Approved hosting budget, private Blob provisioning/upload, actual hosted Range/authorization verification and manual CLI promotion |
| Offline | Cached reader shell and visited paintings; audio streams online | Visited chapter text/timing cache and CDN artwork integration; offline audio is later work |

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
- [ ] **Hosting budget and account:** choose a media provider, initial audience estimate, monthly spending limit, and person responsible for alerts and incident response. Include transcription, artwork revisions, and hosting separately from earlier TTS generation budgets.
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
- [ ] Back up approved source text, masters, manifests, and art references outside the laptop; verify a sample restore. Keep bulk production audio and API receipts outside Git.
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

**Recommended starting point:** keep the app on Vercel and compare Vercel Blob with Cloudflare R2 using the measured traffic model. Vercel Blob reduces account/setup work; R2 is attractive for a public full-book audience because its direct egress is not billed. Select one provider for the beta.

| Option | Useful fit | Decision and verification |
| --- | --- | --- |
| Vercel public Blob | Simple public-media integration with the existing Vercel project | Check account limits and metered delivery costs; full chapter MP3s already total more than the current 1 GB Hobby storage allowance. Public blobs are URL-accessible and CDN-delivered. [Public storage](https://vercel.com/docs/vercel-blob/public-storage), [pricing/limits](https://vercel.com/docs/vercel-blob/usage-and-pricing) |
| Cloudflare R2 Standard + custom domain | Public audio/art delivery with no direct R2 egress charge | Confirm account/domain setup, storage/operation costs, caching rules, and browser headers. Use a production custom domain; r2.dev is intended for development. Other attached metered services can still charge. [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/) |
| Private/paid delivery | Invite-only or purchased access | Protect text, timing data, artwork, and audio. Implement private objects/authorized delivery, entitlement checks, URL refresh, and byte-range playback tests; prove the chosen provider's approach before selection. An obscure public URL or CORS policy does not restrict who can download a book. |

- [ ] Create the selected store/bucket with the publication model's access policy; configure production and preview environments and narrowly scoped upload credentials outside browser code/Git.
- [x] Add invite-only authorized manifest/data delivery; fixtures verify expired/revoked access, logout, and unauthorized requests for every protected asset type. Real-host proof remains pending. Define URL renewal across pauses, new Range requests, and chapter transitions; include any required authorization/streaming service in the capacity model.
- [ ] Upload only approved release assets, using immutable content-hashed keys. Set audio/mpeg, image/webp, and JSON content types; preserve exact encoded audio bytes.
- [ ] Configure long-lived caching for immutable assets and revalidation for the mutable book/release pointer. Check large-file caching limits if offering M4B/ZIP downloads; those packages are much larger than chapter streams.
- [ ] Configure CORS for the app origin and controlled preview origins, allowing GET/HEAD and the needed Range behavior. Expose Content-Range, Accept-Ranges, Content-Length, and ETag for verification where needed. Keep anonymous cross-origin audio compatible with the current audio element. [CORS configuration](https://developers.cloudflare.com/r2/buckets/cors/)
- [ ] Verify real GET Range requests return **206**, exact requested bytes, a correct Content-Range, and an unchanged total size. Test beginning, middle, end, seeking into an unbuffered section, and unsatisfiable ranges.
- [ ] Verify cache behavior and byte ranges together on the actual CDN, including cold responses. Check Content-Length and partial-response behavior rather than assuming an upload proves streamability. [Cloudflare range/cache behavior](https://developers.cloudflare.com/cache/concepts/cache-responses/)
- [ ] Verify every uploaded object's checksum against the release inventory. Keep local hashes as authority; do not assume an object-store ETag is a SHA-256.
- [ ] Point a preview manifest to uploaded assets; test Android Chrome against the real media hostname.
- [ ] Publish the manifest only after every referenced required asset passes verification. Keep prior assets available for open tabs, old timing maps, and rollback.

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
- [ ] Test real HTTPS/CDN delivery: cold/warm loading, throttled network, new seeks, partial playback, connection loss, retry, expired private URLs if used, and blocked automatic continuation.
- [ ] On an **actual Android Chrome phone**, install Lumen, launch its icon, lock the screen, leave/reopen the app, cross chapter boundaries, switch voices, use Bluetooth/headset/media buttons, interrupt playback with a call, and test network loss/reconnection. Current automated phone checks do not establish these physical behaviors.
- [ ] Test portrait/landscape, small screens, maximum text size, hidden artwork, TalkBack, browser Back, and a long listening session. Run a secondary iPhone/browser pass if that platform is promised at launch.
- [ ] Test full-book completion and return after hours/days. Explicitly verify a returning listener is paused at the correct chapter/passage rather than silently restarting.
- [x] Select a private shared-code launch for approximately 50 readers, superseding the proposed 10–25 reader beta. Actual invitation distribution follows verified deployment; both voices are included.
- [ ] Use private hosting request/error and transfer/spend logs for the launch. New product analytics remain later. Monitor request authorization, failed audio/data requests, missing assets and transfer/spend; use reader feedback for playback and resume failures. Avoid collecting raw manuscript text or complete local bookmarks.
- [ ] Collect feedback on narration, image cadence, comfort, resume, installation, and controls; fix blocking issues and repeat only affected checks.

**Exit gate:** no unresolved critical content/playback/resume defect, complete approval inventory, passed targeted/full regression checks, physical Android acceptance, and beta evidence supporting launch. Beta size and performance targets are proposed planning values.

### P8 — Publish the full-book release and establish operations

Owner: product/author and engineering/operations.

- [ ] Finalize cover, metadata, credits, edition descriptions, public access/pricing, AI creation disclosure, support/contact, feedback route, and privacy information for any analytics/accounts.
- [ ] Prepare a release checklist, approved asset manifest, source commit, migration notes, previous release reference, and rollback procedure.
- [ ] Upload immutable media first and verify it; deploy a preview app with the exact intended manifest. Test the preview and stable-origin bookmark migration without overwriting a reader's place.
- [ ] Review the release inventory: all 91 tracks per voice ready, canonical prose complete, every required scene approved, no production aliases/credentials/model sheets exposed.
- [ ] Deploy from web to the existing Vercel project. Current production command:
  ~~~sh
  vercel deploy --prod --yes --scope robert-xies-projects
  ~~~
- [ ] Verify production aliases, public/access-controlled availability as intended, media/data hashes, native audio ranges, PWA installation, first/last tracks, narrator switching, existing bookmarks, and full-book completion.
- [ ] Save deployment/source/hash/check evidence in the release records; commit the reader/export metadata and documentation to the Lumen repo. Keep bulk media outside Git. The Drive test-apps folder remains for Nimblip only.
- [ ] Promote the full-book manifest coherently; do not mutate audio bytes behind existing URLs. Retain old release assets while readers or cached apps can reference them.
- [ ] Follow the first-48-hour owner, feedback, incident and spending thresholds in [private-launch.md](private-launch.md). Configure funded account alerts and record actual checks after deployment.
- [ ] If a blocking regression appears, restore the last working app/manifest and retain valid semantic progress. Fix and republish affected assets under new hashes.
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
- [ ] Review current provider prices and account entitlements at provisioning time. Vercel Blob bills delivery and associated requests/origin traffic; R2 bills storage/operations and has no direct egress fee, while attached services can add charges. Provider cost is not the total operating cost. [Vercel usage](https://vercel.com/docs/vercel-blob/usage-and-pricing), [R2 usage](https://developers.cloudflare.com/r2/pricing/)

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

- [ ] Scope/access/credits/voice and illustration promises match the delivered product.
- [ ] Both promised editions have all 91 ordered tracks, with explicit content approval and immutable hashes.
- [ ] Displayed prose reconstructs the approved manuscript; canonical anchors and pilot migration are verified.
- [ ] Each recording has its own reviewed final-file timing map and reveal cues.
- [ ] Native CDN playback/Range seeking works across chapter boundaries and narrator changes.
- [ ] Returning users resume the right chapter and passage; completion/replay never reset or skip content unexpectedly.
- [ ] Approved artwork coverage, character continuity, alt text, and spoiler boundaries pass review.
- [ ] Phone layout, TalkBack, real Android installation, lock-screen/headset playback, interruption, and retry are accepted.
- [ ] Initial loading and caches are bounded; offline reading claims are accurate; audio remains online unless explicit download functionality is delivered.
- [ ] Release publication, media verification, backups, spending alerts, support, and rollback are in place.
- [ ] Existing production address/bookmarks survive the rollout; new-domain migration is explicit if needed.
- [ ] Release evidence distinguishes automatic checks, human content approval, and physical-device acceptance.

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

External hosting/browser documentation was checked on 10 October 2026. Recheck prices, account limits, and provider behavior when implementing; the architecture and targets above are recommendations, not provisioned infrastructure.
