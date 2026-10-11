# Private release operations

Current access update, 11 October 2026: the code-compatible v18 reader is live at the stable address on deployment `dpl_CqJMWxpYAo9yvVmaqvLfnNzFxefj`, with reviewed implementation `e6b3702be263a7a31a2646e60138f5001c364df0`. Worker `3b3e2aad-c8e4-4460-b7c9-93f877b6330c` serves shared grant version 2. Live new-code access, old-code/session/media denial and normal Chrome 36/36 passed; the v4 book/audio/index, signing keys and expiry are unchanged. Earlier v17/v1 launch and A/B rollback evidence below is historical. Those frontends reject two-word codes, and restoring their Worker policy re-enables old access. Redeploy a compatible reader with the current registry; a compatible v2 rollback has not been exercised. [Current deployment and rotation evidence](../../web/data/deployment.json).

The first full-book release uses the stable Lumen Vercel project, both unchanged 192 kbps editions, female narration by default, generic illustration placeholders, and one shared access code for approximately 50 readers. Character and artwork review continues separately. The private R2 upload, final Worker deployment and final protected-preview HTTP, normal-Chrome and separate native-recovery checks are complete. The private release is now live after the same-release stable-origin promotion/rollback rehearsal and final production Chrome acceptance. Physical-phone acceptance remains a separate follow-up.

The author has now selected and enabled Cloudflare R2. Its private bucket and Worker gateway replace the proposed Blob delivery. See [Cloudflare delivery](cloudflare-delivery.md) for the sole access authority, direct media streaming, upload verification and candidate promotion procedure. Keep Workers on its free plan while measuring hosted behavior.

## Ownership and feedback

Rob Xie owns the private audience, support, hosting budget, release decisions and incident response. The welcome screen directs readers to the person who shared their access code for help or feedback. No email campaign, feedback database or additional analytics service is required for this release.

Use the hosting provider's private request/error and transfer/spend records. Record only the information needed to diagnose delivery: time, response status, asset path, chapter/narrator identifier, and release/deployment identity. Do not copy access codes, session cookies, signed media URL queries, environment values, manuscript passages or complete browser bookmarks into logs or issue reports. Worker observability and Logpush remain disabled; use sanitized status/path evidence rather than enabling credential-bearing logs.

## Promotion evidence

Before publishing the full book, retain a private release record containing:

- The exact reviewed source commit and immutable release ID.
- Book manifest, upload inventory and server-index byte hashes.
- Complete private-object hash verification and real API authorization/HEAD/Range evidence, including original files larger than 4.5 MB and a throttled native-audio transfer.
- Local full-release playback/prose checks and hosted HTTPS cold/warm/reconnection results; record physical Android installation/listening as a separate pending follow-up until it is performed.
- The previous production deployment ID/URL, source commit, app shell identity and matching environment/index pointer. Preserve the pilot reference separately from the two full-release candidates used for the completed production rehearsal.
- Drive backup creation, full local verification and sample restoration records, with cloud sync/checksum status stated separately.

The verified R2 media release is `lumen-private-192-v4`, with 182 original MP3s and 183 JSON files. All 365 assets passed full remote SHA-256 and sample-range verification; the immutable server index was published last and the temporary upload Worker was removed. The full private book is live at the unchanged production address; the historical pilot identity is retained separately. Deploy from `web/` using the existing project configuration; root and web Git deployment guards prevent automatic publication of repository pushes.

## Launch evidence — 11 October 2026

The reviewed release is live. [Cloudflare delivery](cloudflare-delivery.md#final-source-preview-acceptance--11-october-2026) records the exact source, Worker bundle/version/settings, preview receipt hashes and test limits. Final protected-preview acceptance passed HTTP18/18 (365 assets/1,095 samples/full largest-MP3 hash), normal Chrome36/36, and separate throttled/offline same-element recovery in both voices. Preview QA tokens were revoked, local encrypted handles cleared and protection preserved. The native network-simulation proof deliberately disables HTTP cache and blocks the service worker; normal-Chrome checks do not use those overrides.

The full private book is live at the unchanged [production address](https://lumen-phi-five.vercel.app/) as of 11 October 2026. Production A `dpl_9GjWhaJ2D7FjZATQtS2nqpBpjWAW` and B `dpl_FyFkovg79KUP2pvZ4sQSTysydpag` were staged READY with all 54 runtime tuples equal to reviewed source `48f4b56865b1f6e472aa69bf171a4b346e9d0516`. The PR #15 merge `363c67e5b02454c7b28bfc4013a7f10bc0e51fff` has the same tree. Stage receipts are `f7cd4216ac3e2ad779c6566ca49ec6eebf910b3d6a9440357e54808f0937766c` and `d6f387428bdfcbf35e1b92e425fe720e64c91c6f1534cf6daffa3e3c3a65b981`.

The actual stable-origin sequence **A → B → rollback A → B** passed, ending on B. Each phase checked the alias/runtime identity, access denial, private response/cache policy and both voices with saved nonfirst chapter positions. Rehearsal receipt `fb0c5b7713d70f3c86211839a83af809432ebd6b3aefb281be5180317fdfadb0` records no errors. This proves operational same-release rollback and bookmark continuity. Both deployments use the same `lumen-private-192-v4` manifest/index, unchanged audio and compatible bridge/access policy; it does not establish pilot fallback, backwards release compatibility or cross-release migration.

Final normal installed-Chrome acceptance on the stable origin passed **36/36** at 05:42:03 UTC with no browser errors or native-network issues, no request interception and no preview testing token. The raw manifest hash matched the approved v4 bytes; playback, seeking, both voices, navigation, resume, logout, private-cache exclusion and emulated safe-area layouts passed. Receipt `0e10badbddc906bd441ace464880c066378337d77d26db1d20cb962d0e2dcb87` is bound by owner summary `fc24bb3bb4281889defaa5c48ab82fbc3f1f4e8cbe2059f976cd7b19dcf4ca50`. Physical Android installation, headset/lock-screen behavior and full-book human listening are not claimed.

| Check | Completed evidence | Remaining limit or owner follow-up |
| --- | --- | --- |
| Storage and delivery | Full remote object hashes; final-preview HTTP and native/browser acceptance | Account-wide Free limits remain metered; no paid Worker upgrade |
| Stable-origin publication | Exact production B alias/runtime, same-release A/B rollback/re-promotion and final Chrome36/36 | Pilot fallback and backwards compatibility untested |
| QA privacy | Preview testing tokens revoked and encrypted handles cleared; final stable run used no preview token | Continue redacting cookies, codes and signed queries |
| Backup | Drive archive present with expected size; full local hash verification and seven-file local restore | Full cloud ZIP byte/restore proof pending beyond the connector's 256 MiB limit |
| Devices | Emulated phone/iPad/desktop layouts and safe-area controls pass | Physical Android installation/lock-screen/headset acceptance remains open |
| Operations | Rob Xie owns support, usage/error review, budget and incidents | First-48-hour/weekly observations and funded ceiling/account alerts remain to record |

No complete human listening pass, artwork completion or whole-roadmap completion is claimed. Character/story art review and later features remain separate from this authorized placeholder release.

## First 48 hours

Check provider request/error logs, media transfer, budget consumption and reader feedback after promotion, during the next day, and at 48 hours. Record the observations privately. These are release-owner checks; no unattended monitoring job has been created.

- An unauthenticated request returning protected prose, audio or timing data is an immediate incident. Disable the affected access path, correct the configuration, and verify denial before reopening it.
- A repeatable inability to sign in, start audio, seek or resume on two independent browser sessions is a blocking release defect. Redeploy the recorded code-compatible runtime with the currently approved invite policy and its matching immutable index; retain media and browser progress. Use a prior artifact only after its compatibility with the current code and policy has been separately exercised.
- A new missing immutable asset, repeated 5xx response or interrupted chapter transition requires investigation against the recorded inventory and provider logs. Reproduce before changing the release pointer.
- Set an account spending alert at 75% of the approved monthly ceiling, plus an alert for a forecast exceeding that ceiling where the account supports it. At the ceiling, stop new invitations and new media publication while reviewing the funded plan. Do not assume an alert is a hard spending cap or silently interrupt current listening.

The monthly ceiling, account alerts and any repository privacy change remain owner follow-ups. Workers remains Free; no paid upgrade or new spending ceiling is authorized. These follow-ups do not introduce another permission step for the already authorized, technically verified private release. Record the provider's actual alert/limit behavior when configuring the funded operating limits.

## Coherent rollback

A candidate must retain its matching immutable server index and media objects. Record the working runtime/index and current session/invite/media policy privately; do not put credentials in the release receipt. Redeploy a compatible runtime with its matching index while preserving the currently approved access policy. Never restore an archived invite registry or signing configuration that re-enables revoked access.

The initial v17/v1 production rehearsal used two reviewed full-release app deployments A and B, both bound to the same `lumen-private-192-v4` immutable assets, manifest/index and compatible bridge/policy configuration. It promoted A, then B, rolled the stable alias back to A and re-promoted B while retaining paused places in both voices. For a later exercise, require compatible frontends and the current invite/session/media policy in both deployments, then retain the same identity and bookmark checks and verify that revoked codes remain denied. Verify exact alias identities, access denial, first/last tracks and relevant playback/resume behavior after each step. Stop further alias changes if an unexpected third deployment has become current. This same-release A/B exercise does not prove pilot-to-private rollback, backward compatibility, or a cross-release/index migration. The retained pilot identity is a historical reference, not an exercised private-release fallback.

For an actual blocking production incident, redeploy the recorded code-compatible runtime with the current registry and matching index until a rollback compatible with the current policy has actually been exercised. Preserve the current signing policy and old-code/session/media denial; do not restore a former Worker version or archived registry. Once a compatible prior artifact has been exercised under the current policy, a Vercel rollback may be used with its matching index after verifying access denial, the stable alias and saved progress. Retain old objects and local storage. Record recovery evidence; provider rollback does not restore object storage or an external pointer.

A compromised shared code is handled by rotating the grant's code/hash and version, or revoking the grant, then deploying the updated server configuration. Every protected request rechecks the grant. Session-secret compromise requires rotating the signing secret as well. Device sessions otherwise last up to 30 days, capped by the grant's expiry; signing out preserves the listener's local bookmark.
