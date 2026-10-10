# Lumen private reader

The reader is in `dist/`. The full release uses the existing mono, 192 kbps MP3s in both voices: female by default, with the last narrator remembered. It contains 89 story chapters plus opening and closing credits. The current production address remains [lumen-phi-five.vercel.app](https://lumen-phi-five.vercel.app/); release promotion is pending technical verification.

Full prose, timings, audio and future paintings are delivered through authenticated `/api/` endpoints and a private Vercel Blob store. The public app shell contains no full-book prose or invite registry. Invitations establish a secure HttpOnly cookie. Sign out clears the displayed book and audio source while preserving this browser's listening bookmark. The local author audit remains outside the deployment.

## Listening

Open your invitation link, or paste it into the welcome screen. Returning listeners open paused at their saved chapter and time. **Continue** starts playback; **Start over** resets that chapter deliberately. **Chapters**, **Previous**, and **Next** navigate the book. The settings panel lets you change narrator, text size and chapter auto-continuation. Changing narrator maps a measured canonical sentence and fraction into the other recording; uncertain mappings retain the current recording and ask you to choose a passage.

Play/pause, fifteen-second skips, the timeline and playback speed use native browser audio. Clicking a measured sentence seeks to it while preserving paused state. Arrow keys and Space work outside form controls. Only measured intervals receive word/sentence highlighting; inconclusive ASR passages remain faithful plain prose. The audio and timeline continue to work through those intervals. No narration regeneration or estimated cue timestamps are introduced.

Bookmarks use `lumen-book-v2`, with chapter history and completion, canonical sentence position, narrator, final recording hash and exact audio time. The same recording restores exact seconds, including silence. Old pilot bookmarks migrate without deleting their original key; completing the excerpt does not mark the full chapter complete. Storage failures retain usable in-memory progress, and saves merge other tabs' chapter history. Progress is local to this browser and site address; cross-device sync remains later work.

## Phone and installed app

Phone controls have large touch targets, a full-width timeline and safe-area spacing. iPad and desktop use side-by-side art and text; **Art** can hide the generic placeholder. Narrative illustrations remain pending character/artwork review.

In Android Chrome, use the browser menu to install Lumen or add it to the Home screen. The settings panel offers installation help and an install button when Chrome makes it available. iPhone home-screen launch is also supported. Media Session supplies supported system play/pause, seek and chapter controls. Automated Chrome phone, iPad and desktop checks do not constitute a physical-device installation or lock-screen acceptance test.

The service worker caches the public reader shell and up to 40 visited public pilot paintings under `/assets/`. Private `/api/` text, timings, audio and future paintings, plus all Range requests, bypass it. Private reading and listening need a connection; local bookmarks survive disconnection. New workers activate after existing tabs close, without forced reloads during listening. Obsolete shell and prose caches are cleared on activation while the visited public paintings are retained. Private offline artwork remains deferred until it can honor access expiry, revocation and sign-out.

## Development and verification

Run the local author audit with `python3 scripts/audit_server.py` from `web`, then open `http://127.0.0.1:8766/audit`. `python3 scripts/serve.py` serves the static shell and historical pilot assets locally; the private reader needs its API and authenticated media.

```sh
node --test tests/progress.cjs tests/book-controller.cjs
node tests/private-shell.cjs
node tests/book-browser.cjs
npm run test:private
python3 -m unittest discover -s tests -p 'test_*.py'
```

The browser fixture generates short audio in memory and supplies three chapters in both voices. It checks native touch seeking, retries, stale requests, chapter history, narrator switching, completion, private access/logout, source sync gaps and responsive layouts. Playwright and Chrome are required; `PLAYWRIGHT_MODULE` and `LUMEN_QA_OUT` override their locations. Older pilot browser suites remain historical fixtures.

After the private full release has been staged, `node tests/full-release-browser.cjs --registry /absolute/path/to/prepared-registry.json` checks all 182 payloads against canonical prose and briefly plays ten representative original MP3s in a loopback-only Chrome fixture. It verifies actual native seeking, chapter/voice changes, resume and explicit cue gaps. QA evidence stays in a private temporary directory. This check does not certify the hosted backend, physical phones or a full-book listening pass.

The release pipeline and deployment steps are documented in `../docs/backlog/`: content contract, audio release, release exporter, media delivery, private release specification and backup recovery. `server/README.md` documents invitation and private Blob configuration.

## Release operation

Deploy from `web` to the existing `lumen` project in `robert-xies-projects`. The Vercel configuration builds the static shell from `dist/` and deploys the authenticated Node APIs. `.vercelignore` excludes local audits, casting references, production data, secrets, scripts/tests and legacy pilot prose/audio. Preserve local originals for historical tabs and production work.

Use the machine's ignored, owner-only `.env`, encrypted `.env.shared`, and separate `.env.keys` convention. Configure Vercel secrets on the server; never include them in static assets or Git. Upload and verify immutable private media before promoting the corresponding server index. Keep the production origin stable so bookmarks survive releases. Roll back the app and immutable index together.

Bump `SHELL_CACHE` in `dist/sw.js` for runtime changes. Do not use `skipWaiting` or force a reader reload. The Google Drive `test-apps` folder is for Nimblip only; Lumen backups go to the existing private Lumen folder.
