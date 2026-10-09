# Lumen project notes

## Web reader

- The illustrated audiobook pilot is in `web/`; its self-contained runtime is `web/dist/`.
- Use painted realism: charcoal, ivory, restrained gold, visible brushwork, and grounded contemporary settings.
- Use `web/art-direction/cast-bible.json` and versioned character model sheets as continuity references. Distinguish manuscript facts from provisional visual choices. Do not reveal character names or future story details before the narration does.
- Match image changes to narration beats using the final mastered audio clock. Keep displayed prose faithful to the manuscript.
- The Google Drive `test-apps` folder is for Nimblip only. Do not upload Lumen website test builds there.
- The planned hosting provider is Vercel. Keep this pilot local until deployment is requested; the configuration is in `web/vercel.json`.
- For local audio seeking, run `python3 web/scripts/serve.py` from the project root.

## Source files

- Preserve manuscript versions and audiobook production files when working on the reader.
- Never commit `.env`, credentials, downloaded models, local environments, or the bulk audiobook audio/API-response directories.
- The small prepared pilot audio in `web/dist/assets/` is part of the reader deliverable.
