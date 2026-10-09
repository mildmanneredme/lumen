# Lumen illustrated audiobook pilot

An approximately five-minute opening excerpt from Chapter 1 of the v6 manuscript, with the existing Gemini narration, canonical prose, sentence highlighting, and eight GPT-generated paintings in the approved cinematic painted style.

## Run locally

From this directory:

```sh
python3 scripts/serve.py
```

Open `http://127.0.0.1:8765/`. The app has no package installation, build step, backend, live generation, or API key requirement. All runtime resources are in `dist/`.

Use this server for the preview. It serves audio byte ranges (HTTP 206), which let the browser jump to a requested time without downloading everything first. A plain `python3 -m http.server` can make seeks revert to zero. Local preview responses use `Cache-Control: no-store`; scripts and audio also have versioned URLs so previously cached controls are refreshed.

## Reader controls

- Play/pause, rewind or advance 15 seconds, scrub, and change playback speed.
- Click a sentence to seek to its narration. If paused, the reader stays paused.
- Scroll to read ahead; Follow text returns to the narration.
- Adjust text size with Aa. Speed and text size are remembered on this browser.
- Returning listeners see **Continue from…** and **Start over**. The audio opens paused at the saved place, with the corresponding painting and highlighted sentence. Continue starts listening; Start over resets the position and stays paused.
- Navigation before the audio is ready is queued. Repeated 15-second skips accumulate, and the latest timeline selection takes precedence. Loading feedback remains visible on phones.
- Space toggles playback outside controls; left/right arrows seek 15 seconds. Reduced motion disables scene crossfades.

## Phone and installed app

The phone layout has a full-width touch timeline, large playback controls, adjustable text, and safe-area spacing for notches and home indicators. Rotate to landscape for artwork beside the prose. **Art** hides the illustration when you want more reading space; this preference is remembered. Media Session supplies playback metadata and supported system play/pause/seek controls.

Open the production site in Chrome. In the Aa panel, **Install Lumen** opens Chrome's install prompt when available; otherwise **How to install** shows the browser's steps. There are no automatic install prompts.

- Android Chrome: menu → **Install and create shortcut** → **Install**. Older versions may say **Add to Home screen**.
- iPhone Chrome: **Share** → **Add to Home Screen** → **Add**.

Chrome documents [Android installation](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DAndroid&hl=en) and [iPhone home-screen installation](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DiOS&hl=en). Launching the saved icon opens Lumen as a standalone app when supported. Browser and OS behavior can vary; physical phone installation has not yet been verified.

The service worker saves the small reader shell and paintings you visit. Cached text and artwork remain readable offline, subject to browser storage eviction. **Audio requires a connection** and streams through Chrome's native media stack, including byte-range seeking. The bookmark survives offline visits, and Play retries after reconnection. Releases activate after existing reader tabs close, without interrupting current listening.

The eight WebP paintings are about 89% smaller than the original PNGs, with the same dimensions and framing. Only the current painting and the next one load ahead; Save-Data disables the next-image prefetch. Original PNGs remain available for older open tabs. Run `python3 scripts/optimize_art.py` after replacing artwork; `data/artwork-optimization.json` records the output sizes.

## Saved place and narrator editions

`dist/progress.js` stores a versioned bookmark under `lumen-reader-v1`: book, manuscript, chapter, canonical sentence, fraction through that sentence, narrator, recording hash, exact audio time, and completion. Position saves roughly every two seconds during playback, including background audio, and on pause, seek, page hide, or tab hide. Preferences use a separate key. Existing pilot bookmarks migrate without deleting their original key. Completed excerpts remain completed; loading the full chapter later maps the listener to the end of the excerpt rather than skipping the rest of Chapter 1.

The same recording restores exact seconds. A replacement recording or another narrator uses its own sentence timings to map the bookmark back to the same story passage. Missing or incompatible manuscript anchors are never guessed from elapsed seconds. The browser stays usable if storage is unavailable. This is local to the browser and site address; cross-device or localhost-to-production transfer will need an account-based sync or explicit bookmark import.

The full male (Charon) and female (Autonoe) editions are pending final audio and reader timing. The current pilot uses Charon. `data/narration-editions.json` records the planned editions; it does not enable unfinished tracks. Once the audio is ready, export each narrator's chapter audio with a verified SHA-256 and its own canonical sentence/scene timing map. Share manuscript IDs, character references, and painting assets between voices. Add the voice picker when both reader exports are ready, and map the current sentence when switching. Full-book illustration work is on hold until then.

## Timing and production

The excerpt ends at 320.190 seconds after a complete paragraph and a measured quiet gap. It preserves 11 manuscript paragraphs, with 60 sentence cues and eight scene cues. `data/alignment-report.json` records source hashes and checks; timings use existing local ASR word timestamps mapped to the final mastered audio. They are approximate and have not received a full human listening review.

The visual treatment includes present-day scenes, memories, and explicit metaphors for the cognitive grid and people becoming clay. Imagery changes at the corresponding narrated line rather than at an arbitrary text-page boundary. The unnamed suspect is shown only when introduced.

`art-direction/cast-bible.json` separates canonical character facts from provisional design choices and registers stable character IDs, references, wardrobe variants, and reveal boundaries. The pilot model sheets are in `art-direction/models/`; future named cast entries remain planned until their model sheets are created and reviewed. Image-generation prompts are in `data/painting-prompts.json`.

`scripts/prepare_pilot.py` regenerates the text/audio export and browser data from local v6 production files and ignored ASR caches. Run `python3 scripts/prepare_pilot.py --verify-only` to check the canonical prose, cue ordering, exported audio hash, and equality of prepared/browser data. The prepared runtime data is in `dist/data/chapter-001.js` and audio in `dist/assets/chapter-001-pilot.mp3`.

## Verification

`node --test tests/progress.cjs` checks persistence and mapping without a browser. With Playwright and Chrome available, `node tests/browser-smoke.cjs`, `node tests/navigation-real-input.cjs`, and `node tests/resume-navigation.cjs` verify actual audio playback, painting/prose synchronization, mouse and touch navigation, slow loading, resume, completion, and revised recordings. `node tests/mobile-pwa.cjs` covers five phone viewports, touch controls, safe areas, Chrome installability, install choices, offline reading, native audio ranges, error recovery, and system media controls. These use Chrome emulation, not a physical phone. Set `PLAYWRIGHT_MODULE` or `CHROME_PATH` when needed; `LUMEN_QA_URL` targets a deployed site and `LUMEN_QA_OUT` chooses the report directory. The real-input suite uses native mouse and touch events rather than synthesizing range changes.

## Vercel deployment

Production: [lumen-phi-five.vercel.app](https://lumen-phi-five.vercel.app/). Project: `lumen` in `robert-xies-projects`, created on 9 October 2026. This release is the opening pilot.

Releases deploy directly with the Vercel CLI from `web`:

```sh
vercel deploy --prod --yes --scope robert-xies-projects
```

On a fresh checkout, first run `vercel link --yes --project lumen --scope robert-xies-projects` from `web`. Local project identifiers stay in ignored `.vercel/`. Current releases use the CLI; GitHub is the source repository.

`vercel.json` selects the Other framework preset, no build command, and `dist` as the output directory. `.vercelignore` permits only `dist/` and the deployment configuration in the CLI upload. See [Vercel's configuration documentation](https://vercel.com/docs/project-configuration/vercel-json) and [deployment exclusions](https://vercel.com/docs/deployments/vercel-ignore). If Git-based automatic deployment is added later, set the repository's Root Directory to `web`.

Use the stable production address for listening bookmarks. Each site origin has its own browser storage; existing localhost bookmarks are separate. Deployment verification is recorded in `data/deployment.json`.

When changing runtime HTML, JavaScript, or CSS, bump `SHELL_CACHE` in `dist/sw.js`. Keep the worker and manifest revalidation headers in `vercel.json`. Do not force worker activation or reload a listener's page.
