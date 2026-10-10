# Local narration audit

From the project root, run:

```sh
python3 web/scripts/audit_server.py
```

Open **http://127.0.0.1:8766/audit** on this computer. The server binds only to
loopback. It serves a fixed UI and hash-verified, allowlisted production audio;
it does not expose the project directory. It is excluded from the Vercel
deployment because it lives outside `web/dist`.

The queue contains 377 OpenAI findings from the partial 142/496-chunk review,
plus 755 unresolved/inconclusive Charon local-ASR findings. These evidence
sources remain separate. OpenAI windows use mastered-chapter seconds; local-ASR
windows use original source-WAV seconds and are labelled accordingly. Estimated
word positions are recognizer evidence, not exact audible-error boundaries.

Listen, select **Keep as recorded**, **Regenerate**, or **Needs discussion**,
and save a note. Regeneration requires a specific correction note. **Save &
next** advances through the current filter. Feedback persists in the ignored
`Audiobook/author-audit/feedback.json`, with recording/source/request identities,
revisions, and append-only decision history. Back up this file with the private
production assets. A changed report binding resets its displayed verdict to
pending; changed audio is rejected before playback, saving, or repair export.
Concurrent tabs must explicitly review newer feedback before overwriting it.
The client also submits the exact review binding it displayed. Changed windows,
wording, or provenance require a new listening review even on the same audio.

**Export regeneration queue** downloads a repair plan grouped by selected source
take. Each item preserves the exact prepared text, model, voice, style, source
hashes, and your notes. Export makes no paid requests and never changes selected
production takes. The original model/voice/style/text digest is recomputed before
export, so edited inputs cannot reuse an old request hash. After review, a repair executor must create a fresh take,
recheck it, rebuild the affected chapter, and return it for listening. Existing
audio and feedback must remain available. Do not run the old generator's
`prepare()` against the selected manifest: it would discard later split takes.

Keeping one flagged passage is not whole-chapter content approval. The remaining
354 OpenAI transcription chunks still require credits; the audit remains useful
while those checks are pending. No full-book audio or author notes are published.

Validation:

```sh
python3 -m unittest discover -s web/tests -p test_audit_server.py
node web/tests/audit-browser.cjs
```

The browser suite uses disposable fixtures; it does not write author feedback.
