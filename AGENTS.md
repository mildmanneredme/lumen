# Lumen project notes

## Web reader

- The illustrated audiobook pilot is in `web/`; its self-contained runtime is `web/dist/`.
- Use painted realism: charcoal, ivory, restrained gold, visible brushwork, and grounded contemporary settings.
- Use `web/art-direction/cast-bible.json` and versioned character model sheets as continuity references. Distinguish manuscript facts from provisional visual choices. Do not reveal character names or future story details before the narration does.
- Match image changes to narration beats using the final mastered audio clock. Keep displayed prose faithful to the manuscript.
- Hold full-book illustration expansion until the male and female audio editions are ready. Each narrator needs a separate timing map tied to its recording hash; share canonical sentence IDs and paintings.
- Preserve reader bookmarks across audio revisions by mapping the canonical sentence position. For the same recording, restore the exact audio time; never reset completed excerpts to the beginning automatically.
- The Google Drive `test-apps` folder is for Nimblip only. Do not upload Lumen website test builds there.
- The live pilot is on Vercel project `lumen` in `robert-xies-projects`: `https://lumen-phi-five.vercel.app/`. Deploy with the CLI from `web/`; the configuration is in `web/vercel.json`. Keep production URLs stable so browser bookmarks survive releases.
- For local audio seeking, run `python3 web/scripts/serve.py` from the project root.

## Source files

- Preserve manuscript versions and audiobook production files when working on the reader.
- Never commit `.env`, credentials, downloaded models, local environments, or the bulk audiobook audio/API-response directories.
- The small prepared pilot audio in `web/dist/assets/` is part of the reader deliverable.
