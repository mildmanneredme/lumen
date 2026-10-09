# Lumen illustrated audiobook pilot

An approximately five-minute opening excerpt from Chapter 1 of the v6 manuscript, with the existing Gemini narration, canonical prose, sentence highlighting, and eight GPT-generated paintings in the approved cinematic painted style.

## Run locally

From this directory:

```sh
python3 scripts/serve.py
```

Open `http://127.0.0.1:8765/`. The app has no package installation, build step, backend, live generation, or API key requirement. All runtime resources are in `dist/`.

## Reader controls

- Play/pause, rewind or advance 15 seconds, scrub, and change playback speed.
- Click a sentence to seek to its narration. If paused, the reader stays paused.
- Scroll to read ahead; Follow text returns to the narration.
- Adjust text size with Aa. Playback position, speed, and text size are remembered on this browser.
- Space toggles playback outside controls; left/right arrows seek 15 seconds. Reduced motion disables scene crossfades.

## Timing and production

The excerpt ends at 320.190 seconds after a complete paragraph and a measured quiet gap. It preserves 11 manuscript paragraphs, with 60 sentence cues and eight scene cues. `data/alignment-report.json` records source hashes and checks; timings use existing local ASR word timestamps mapped to the final mastered audio. They are approximate and have not received a full human listening review.

The visual treatment includes present-day scenes, memories, and explicit metaphors for the cognitive grid and people becoming clay. Imagery changes at the corresponding narrated line rather than at an arbitrary text-page boundary. The unnamed suspect is shown only when introduced.

`art-direction/cast-bible.json` separates canonical character facts from provisional design choices and registers stable character IDs, references, wardrobe variants, and reveal boundaries. The pilot model sheets are in `art-direction/models/`; future named cast entries remain planned until their model sheets are created and reviewed. Image-generation prompts are in `data/painting-prompts.json`.

`scripts/prepare_pilot.py` regenerates the text/audio export from local v6 production files and ignored ASR caches. Run `python3 scripts/prepare_pilot.py --verify-only` to check the canonical prose, cue ordering, and exported audio hash. The prepared runtime data is in `dist/data/chapter-001.js` and audio in `dist/assets/chapter-001-pilot.mp3`.

## Deploy to Vercel when ready

Import the Lumen repository and set **Root Directory** to `web`. `vercel.json` selects the Other framework preset, no build command, and `dist` as the output directory. Only runtime files in `dist` are served. See [Vercel's configuration documentation](https://vercel.com/docs/project-configuration/vercel-json).

This pilot is prepared locally; no hosting deployment or Google Drive test package is required.
