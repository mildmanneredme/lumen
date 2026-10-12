# Lumen private reader

Illustration release, 11 October 2026: the stable URL now serves
`lumen-illustrated-192-v1` with 1,379 paintings / 2,758 WebPs and all 182
unchanged recordings through private R2. Protected candidate acceptance passed
108/108 assertions; the cross-release rehearsal passed all 268 functional
checks but failed its final header-read shutdown. Fresh stable Chrome passed
105/105 with complete CDP Worker headers and normal context closure. Final
provider postflight and read-only production-routing reconciliation passed. [Illustration release identities
and evidence](../docs/backlog/illustration-release.md) distinguish confirmed upload/deployment facts from
unrecorded publication checks. The v4 launch and access-rotation evidence
below remains historical provenance.

Historical v4 access update, 11 October 2026: the code-compatible v18 reader was live at the stable address on deployment `dpl_CqJMWxpYAo9yvVmaqvLfnNzFxefj`, with reviewed implementation `e6b3702be263a7a31a2646e60138f5001c364df0`. Worker `3b3e2aad-c8e4-4460-b7c9-93f877b6330c` serves shared grant version 2. Live new-code access, old-code/session/media denial and normal Chrome 36/36 passed; the v4 book/audio/index, signing keys and expiry are unchanged. Earlier v17/v1 launch and A/B rollback evidence below is historical. Those frontends reject two-word codes, and restoring their Worker policy re-enables old access. Redeploy a compatible reader with the current registry; the later compatible cross-release v2 rehearsal is recorded separately in the illustrated release evidence. [Current deployment and rotation evidence](data/deployment.json).

The reader is in `dist/`. The full release uses the existing mono, 192 kbps MP3s in both voices: female by default, with the last narrator remembered. It contains 89 story chapters plus opening and closing credits. The current production address remains [lumen-phi-five.vercel.app](https://lumen-phi-five.vercel.app/); the illustrated full private book is live as of 11 October 2026 after the actual cross-release promotion/rollback/recovery sequence. Fresh stable Chrome passed 105/105 and final provider postflight passed; persisted production routing is reconciled. [Launch evidence and remaining checks](../docs/backlog/private-launch.md) record the limits.

Full prose and timings use authenticated `/api/` endpoints backed by private Cloudflare R2. A Cloudflare Worker owns shared-code sessions and access policy; Vercel returns private redirects for audio and paintings so media streams directly from Cloudflare. The public app shell contains no full-book prose or invite registry. Invitations establish a secure HttpOnly cookie. Sign out clears the displayed book and audio source while preserving this browser's listening bookmark. The local author audit remains outside the deployment.

## Listening

Enter the shared access code on the welcome screen. Legacy invitation links remain supported. Returning listeners open paused at their saved chapter and time. **Continue** starts playback; **Start over** resets that chapter deliberately. **Chapters**, **Previous**, and **Next** navigate the book. The settings panel lets you change narrator, text size and chapter auto-continuation. Changing narrator maps a measured canonical sentence and fraction into the other recording; uncertain mappings retain the current recording and ask you to choose a passage.

Play/pause, fifteen-second skips, the timeline and playback speed use native browser audio. Clicking a measured sentence seeks to it while preserving paused state. Arrow keys and Space work outside form controls. Only measured intervals receive word/sentence highlighting; inconclusive ASR passages remain faithful plain prose. The audio and timeline continue to work through those intervals. No narration regeneration or estimated cue timestamps are introduced.

Bookmarks use `lumen-book-v2`, with chapter history and completion, canonical sentence position, narrator, final recording hash and exact audio time. The same recording restores exact seconds, including silence. Old pilot bookmarks migrate without deleting their original key; completing the excerpt does not mark the full chapter complete. Storage failures retain usable in-memory progress, and saves merge other tabs' chapter history. If an audio revision cannot map a saved passage, the reader keeps the bookmark and offers **Start this chapter from the beginning**. This deliberate action replaces only that chapter’s bookmark after a successful load; failed or cancelled recovery preserves it. Progress is local to this browser and site address; cross-device sync remains later work.

## Phone and installed app

Phone controls have large touch targets, a full-width timeline and safe-area spacing. iPad and desktop use side-by-side art and text; **Art** can hide the artwork panel. The illustrated release adds 1,379 narration-bound paintings with full-size and phone WebPs through the private R2 gateway. It is published at the stable URL with fresh stable Chrome 105/105 and final provider postflight passed, with persisted production routing reconciled; the preceding v4 release used generic placeholders. Independently measured image cues can reveal a painting while uncertain sentence timing stays unhighlighted. [Generated imagery integration](../docs/backlog/imagery-integration.md) records the collection, preparation commands and review status.

In Android Chrome, use the browser menu to install Lumen or add it to the Home screen. The settings panel offers installation help and an install button when Chrome makes it available. iPhone home-screen launch is also supported. Media Session supplies supported system play/pause, seek and chapter controls. Automated Chrome phone, iPad and desktop checks do not constitute a physical-device installation or lock-screen acceptance test.

The service worker caches the public reader shell and up to 40 visited public pilot paintings under `/assets/`. Private `/api/` text, timings, audio and paintings, plus all Range requests, bypass it. Private reading and listening need a connection; local bookmarks survive disconnection. New workers activate after existing tabs close, without forced reloads during listening. Obsolete shell and prose caches are cleared on activation while the visited public paintings are retained. Private offline artwork remains deferred until it can honor access expiry, revocation and sign-out.

## Development and verification

Run the local author audit with `python3 scripts/audit_server.py` from `web`, then open `http://127.0.0.1:8766/audit`. `python3 scripts/serve.py` serves the static shell and historical pilot assets locally; the private reader needs its API and authenticated media.

```sh
node --test tests/progress.cjs tests/book-controller.cjs
node tests/private-shell.cjs
node tests/book-browser.cjs
node tests/reader-controls.cjs
node tests/full-release-browser.cjs --self-test-shell
npm run test:private
python3 -m unittest discover -s tests -p 'test_*.py'
```

The browser fixture generates short audio in memory and supplies three chapters in both voices. It checks native touch seeking, retries, stale requests, chapter history, narrator switching, completion, private access/logout, source sync gaps and responsive layouts. Playwright and Chrome are required; `PLAYWRIGHT_MODULE` and `LUMEN_QA_OUT` override their locations. Older pilot browser suites remain historical fixtures.

Reader controls use a shared, locally drawn SVG set in `dist/icons.js` and a final style layer in `dist/controls.css`. Decorative icons inherit the charcoal, ivory and gold palette; changing labels preserves the icons and native control semantics. `reader-controls.cjs` checks icon state changes, accessible names, keyboard focus, reduced motion and phone/tablet/desktop geometry using loopback-only historical text and generated audio. It also captures the welcome screen, settings, chapter list and painted reader for visual review.

The release fixture records and verifies the exact control stylesheet and icon script it serves. `full-release-browser.cjs --self-test-shell` checks those local assets and anonymous private-book denial without loading the full release; it does not certify the book or hosted deployment.

After the private full release has been staged, `node tests/full-release-browser.cjs --registry /absolute/path/to/prepared-registry.json` checks all 182 payloads against canonical prose and briefly plays ten representative original MP3s in a loopback-only Chrome fixture. It verifies actual native seeking, chapter/voice changes, resume and explicit cue gaps. QA evidence stays in a private temporary directory. This check does not certify the hosted backend, physical phones or a full-book listening pass.


For hosted Chrome acceptance, `node tests/cloudflare-browser.cjs --self-test` checks its private-input and credential-redaction contract. The hosted mode requires explicit candidate/Worker origins, `LUMEN_QA_EXPECTED_RELEASE_ID`, the prepared raw `/api/book` byte hash in `LUMEN_QA_EXPECTED_MANIFEST_SHA256`, a privately supplied access code and a fresh owner-only output directory under `/private/tmp`. It requires installed Google Chrome and records its version. Portrait checks require a full-width timeline and nonzero simulated safe-area geometry; physical-phone acceptance remains separate. A protected preview may need an authorized temporary Vercel testing token; preserve deployment protection and revoke that token after testing. This suite checks representative native MP3 playback, seeking, resume, both voices, private-cache exclusion and phone/iPad/desktop layouts. Its self-test alone establishes none of those hosted behaviors. `node tests/cloudflare-browser.cjs --self-test-browser` additionally renders the actual reader HTML/CSS in installed Chrome with nonzero CDP safe-area overrides and blocked network requests. That offline geometry check does not test the protected Vercel candidate or physical hardware.

The release pipeline and deployment steps are documented in `../docs/backlog/`: content contract, audio release, release exporter, media delivery, private release specification and backup recovery. `server/README.md` documents invitation configuration and the retained Blob fallback. [Cloudflare delivery](../docs/backlog/cloudflare-delivery.md) describes the selected hosting and its actual verification status.

## Release operation

Deploy from `web` to the existing `lumen` project in `robert-xies-projects`. Both repository-root and web `vercel.json` files set `git.deploymentEnabled: false`, preventing automatic changes to the live reader when PRs merge. Deployment and explicit promotion use the CLI after media verification; see [Vercel’s Git configuration](https://vercel.com/docs/project-configuration/git-configuration). The Vercel configuration builds the static shell from `dist/` and deploys the authenticated Node APIs. `.vercelignore` excludes local audits, casting references, production data, secrets, scripts/tests and legacy pilot prose/audio. Preserve local originals for historical tabs and production work.

Local deployment checks use `@vercel/routing-utils`. A scoped override pins its
`path-to-regexp` dependency to patched version 6.3.0; see the [maintainer advisory](https://github.com/pillarjs/path-to-regexp/security/advisories/GHSA-9wv6-86v2-598j).
Run `npm ci` and `npm run test:private` from `web/` to verify the lockfile and
compiled private-asset routes. The full dependency audit currently reports zero
vulnerabilities.

Use encrypted, ignored, owner-only `.env` and `.env.shared` files with a separate owner-only `.env.keys`. Run local tools through `dotenvx run -f .env.shared -- ...` from the matching directory. Configure Vercel secrets on the server; never include them in static assets or Git. Upload and verify immutable private media before promoting the corresponding server index. Keep the production origin stable so bookmarks survive releases. Retain the matching app, Worker and immutable index for each release. The static previous-release bridge preserves still-open reader asset paths in both rollout directions; use the current access policy throughout. The encrypted local environments retain the v4 operator context, while the reviewed illustrated-media operator pins its separate Worker and index explicitly. See [illustration release operations](../docs/backlog/illustration-release.md).

Bump `SHELL_CACHE` in `dist/sw.js` for runtime changes. Do not use `skipWaiting` or force a reader reload. The Google Drive `test-apps` folder is for Nimblip only; Lumen backups go to the existing private Lumen folder.
