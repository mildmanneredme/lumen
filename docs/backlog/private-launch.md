# Private release operations

The first full-book release uses the stable Lumen Vercel project, both unchanged 192 kbps editions, female narration by default, generic illustration placeholders, and one shared access code for approximately 50 readers. Character and artwork review continues separately. This guide records the operational plan; it does not claim a deployment, paid provisioning, physical-phone acceptance, or exercised production rollback.

## Ownership and feedback

Rob Xie owns the private audience, support, hosting budget, release decisions and incident response. The welcome screen directs readers to the person who shared their access code for help or feedback. No email campaign, feedback database or additional analytics service is required for this release.

Use the hosting provider's private request/error and transfer/spend records. Record only the information needed to diagnose delivery: time, response status, asset path, chapter/narrator identifier, and release/deployment identity. Do not copy access codes, session cookies, environment values, manuscript passages or complete browser bookmarks into logs or issue reports.

## Promotion evidence

Before publishing the full book, retain a private release record containing:

- The exact reviewed source commit and immutable release ID.
- Book manifest, upload inventory and server-index byte hashes.
- Complete private-object hash verification and real API authorization/HEAD/Range evidence, including original files larger than 4.5 MB and a throttled native-audio transfer.
- Local full-release playback/prose checks, HTTPS cold/warm and reconnection results, and separate actual Android installation/listening acceptance.
- The immediate previous production deployment ID/URL, source commit, app shell identity and matching environment/index pointer. Preserve the pilot as the initial fallback.
- Drive backup creation, full local verification and sample restoration records, with cloud sync/checksum status stated separately.

The current locally verified media release is `lumen-private-192-v4`, with 182 original MP3s and 183 JSON files. The current production pilot remains unchanged until candidate hosting verification passes. Deploy from `web/` using the existing project configuration; root and web Git deployment guards prevent automatic publication of repository pushes.

## First 48 hours

Check provider request/error logs, media transfer, budget consumption and reader feedback after promotion, during the next day, and at 48 hours. Record the observations privately. These are release-owner checks; no unattended monitoring job has been created.

- An unauthenticated request returning protected prose, audio or timing data is an immediate incident. Disable the affected access path, correct the configuration, and verify denial before reopening it.
- A repeatable inability to sign in, start audio, seek or resume on two independent browser sessions is a blocking release defect. Restore the coherent previous deployment and index while retaining the immutable media and browser progress.
- A new missing immutable asset, repeated 5xx response or interrupted chapter transition requires investigation against the recorded inventory and provider logs. Reproduce before changing the release pointer.
- Set an account spending alert at 75% of the approved monthly ceiling, plus an alert for a forecast exceeding that ceiling where the account supports it. At the ceiling, stop new invitations and new media publication while reviewing the funded plan. Do not assume an alert is a hard spending cap or silently interrupt current listening.

The monthly ceiling, available account credits and repository privacy change still require the outstanding author decision. Confirm the provider's actual alert/limit behavior before provisioning; no new budget is implied by these thresholds.

## Coherent rollback

A candidate must retain its own immutable server index and media objects. Record the working app's exact index environment and session/invite configuration privately; do not put credentials in the release receipt. Switching an app deployment must restore its matching index pointer rather than leaving an independently changed pointer in place.

Exercise this on the candidate/preview first: save progress in each voice, switch to the prior coherent app/index pair, reopen paused at the same passage, and switch back. Confirm first/last tracks, native seeking, chapter transitions and anonymous asset denial. A preview rehearsal is separate from a production rollback.

For an actual blocking production incident, use the recorded eligible prior deployment with the Vercel rollback command from `web/`, restore any independently changed index/environment coherently, and verify the stable production alias and saved progress. Do not delete old objects or clear local storage. Record the rollback and subsequent promotion evidence; provider rollback alone does not restore object storage or an external pointer.

A compromised shared code is handled by rotating the grant's code/hash and version, or revoking the grant, then deploying the updated server configuration. Every protected request rechecks the grant. Session-secret compromise requires rotating the signing secret as well. Device sessions otherwise last up to 30 days, capped by the grant's expiry; signing out preserves the listener's local bookmark.
