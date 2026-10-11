# Illustrated private release — 11 October 2026

Release status: **live and verified**.
The stable production origin now points to the illustrated deployment after a
verified retained-v4 → illustrated → retained-v4 → illustrated rehearsal.
Protected candidate acceptance passed 108/108 assertions; all 268 historical
functional checks passed, but that run's final request-header shutdown failed.
The original failed report is preserved. Fresh stable Chrome acceptance passed 105/105 assertions with complete
CDP Worker header evidence and normal context closure. Final provider postflight and read-only production-routing reconciliation
also passed. Historical launch and access-rotation records are retained separately.

The production address remains
[lumen-phi-five.vercel.app](https://lumen-phi-five.vercel.app/). The release uses
the existing private R2 bucket `lumen-private`; it adds no public image bucket,
image proxy or separate image service.

## Release and upload evidence

`lumen-illustrated-192-v1` contains 91 tracks: 89 story chapters and opening and
closing credits, with 182 unchanged mono 192 kbps recordings and Autonoe as the
default narrator. It adds 1,379 paintings with full-size and phone variants:
2,758 WebPs / 428,143,190 bytes. Each narrator has its own recording-bound scene
clock; all 2,758 reveal clocks are measured and strictly increasing. Canonical
sentence IDs, prose, original sentence timings, audio hashes and reading extents
are preserved.

The author authorized publication with “Lets do the illustration release and
deployment.” The private release copies use source-bound `accepted-with-note`
records citing that authorization. They do not claim a new individual visual
inspection, human approval of every onset, or a complete book listening pass.
Original pending review packets, production selections, PNGs and audio remain
preserved privately.

| Immutable identity | Value |
| --- | --- |
| Book manifest SHA-256 | `b2d74735594a5f11b1e5c6e6dff563227f0090642b0d0447d747901bbd4ac3e4` |
| Upload inventory SHA-256 | `e6acba06f544088a6d40dbe6bc8b20958c38c53408bfdfb16ca771677dea0227` |
| Server index SHA-256 | `02a2567d48185b1fa4a745c92c52aff10c3fd76f80ec8874e444bddc5369a76a` |
| Server index bytes | 1,242,385 |
| Inventory | 3,123 assets: 182 MP3s, 2,758 WebPs and 183 JSON objects |
| New upload | 440,923,460 bytes, including the index |
| Prior MP3 reuse | 182 objects / 2,657,554,540 bytes from `lumen-private-192-v4` |
| Complete verification reads | 3,108,071,856 bytes, including 9,369 sample ranges |

New objects were written only under
`lumen/releases/lumen-illustrated-192-v1/`. The prior hashed MP3 keys were
read-only and freshly verified in full, with all three sample ranges, rather
than duplicated or overwritten. Every inventory asset passed complete remote
SHA-256, sample and HEAD verification; the immutable index was published last.
The receipt's `accessPolicyVerified:false` remains intentional: administrative
upload integrity is separate from reader authorization and hosted acceptance.

Private upload receipt `d7c4058d3464443d8bb20aeb2e81aeca8b4539d52d426c9b4455821f365a5694`
records completion at 08:38:41 UTC. Cleanup receipt
`73699a8e4f67f35c05e4ad13be3cf483038e826f23ff57cbd569940efb0f3851`
records removal of the exact temporary `lumen-illustrations-upload-v1` Worker at
08:40:16 UTC. The bucket, release objects and existing media Worker were retained.
These ignored records are under
`Audiobook/author-audit/imagery-release-v1/operations/receipts/2026-10-11T07-26-53-138Z-0a7edfad-92e2-4a86-87e0-7397ae8bdb35/`.

## Source-bound deployment artifacts

The Vercel public runtime and Cloudflare backend have separate reviewed source
identities. The three READY Vercel stages each bind the same 54 uploaded file
tuples to `880bd1872478d92dc47e734398813eb09c4ba15b`. The illustrated media Worker
binds to `a595efd03fe634366e65796c6f1e6ebd920b3ee0`, merged into main as
`2aedf89e4d2becd3a98f5285ae9c50ae82cec8ef` in PR #19. This does not claim that
all Vercel upload bytes equal the later main tree; `README.md` and
`server/private-access.cjs` changed after the runtime stage was frozen.

| Artifact | Verified staged identity | Hosted publication status |
| --- | --- | --- |
| Protected preview | `dpl_ArmhmXmvpK2MhgLMbun8TXjwpUyz` / `lumen-dlwzsk7v0-robert-xies-projects.vercel.app` | Candidate verified: 108/108 assertions |
| Illustrated production stage | `dpl_4DfmAArqZN9tug2waKLLostcGNzd` / `lumen-2q0xd1zhp-robert-xies-projects.vercel.app` | Stable origin recovered; fresh stable Chrome 105/105 passed |
| Retained v4 production stage | `dpl_B1ZNDHFYWi1ZzS6xT2JqLJcfc1x6` / `lumen-6fokoqvzz-robert-xies-projects.vercel.app` | Actual rollback/recovery receipts verified |

The illustrated backend is `lumen-media-illustrated-v1` at
`https://lumen-media-illustrated-v1.lumen-robxie.workers.dev`, version
`acc6ed0a-af65-4bee-a88d-7f408d26368d`, with bundle SHA-256
`fdef1dd6ee6cf010936eda703ef8807971e9b03a968122e598ea95cea7291f42`.
Deployment receipt
`966c737aac6bf440f7af0ac84a89c3e1080ae2d50d22667ace4425115dbd44ca`
records 100% traffic at 08:41:51 UTC, the new immutable index, exact preview
origins, and a copy of the current version-2 shared access policy. The plaintext
owner code was excluded. Worker observability and Logpush remain disabled; no
paid Workers upgrade or subscription change was made. This provisioning
receipt does not establish browser transport or Free-tier performance.

The old `lumen-media` Worker remains on current-policy version
`3b3e2aad-c8e4-4460-b7c9-93f877b6330c` and the retained v4 index
`c1fcab6251ef94b01f71e6e8425fb456d27ac6583e9b3e54d9011f4fae23dc3f`.
Its policy was not reverted or replaced by the illustrated deployment.

## Protected candidate acceptance

The final protected candidate report is `candidate-verified`, with 108/108
assertions and no browser errors or native-ledger issues. Its SHA-256 is
`dea4bd03e7fc1a2261af5140bce8598e00cce82a60a96264ec03272675fb5f15`
(381,261 bytes). The exact report is preserved under
`Audiobook/author-audit/imagery-release-v1/operations/qa-archive/2026-10-11-vbtbfF/browser/candidate-report.json`.

Five Chrome layout/narrator cases passed: phone Autonoe, phone Charon, tablet
Autonoe, desktop Autonoe and desktop Charon. They verified decoded selected
phone/full artwork at measured voice clocks, no early reveal, only active/next
painting requests, real native MP3 playback and private Worker byte ranges,
exact paused same-recording reload time, private Cache Storage exclusion,
sign-out clearing, and signed-out painting/audio denial. Six representative
protected WebP hash checks passed. The complete run observed 27,752,654 native
network bytes; it used no request interception or response replacement.

The separate image-failure case blocked one exact unvisited painting URL using
Chrome's native network controls. Two bounded attempts left the paused native
audio clock and element intact. Removing the block and issuing the explicit
online event recovered the painting with its exact immutable hash; private
Cache Storage remained empty.

The report binds the frozen harness
`0d6ae76cd3f5be0ac74d72b3ab771450554e6986483827bab01ef9f0dc43ee3e`;
the owner-only wrapper is
`ade88d660959e3d861eb69444ee89f3a66fda278cfed7017fc8cf8c99d452335`.
This is representative hosted Chrome evidence. It does not claim a browser
checksum of every painting or the private index, physical-device acceptance,
a full human listening pass, or individual human onset approval. Complete
object inventory integrity is established separately by the upload receipt.

## Production rehearsal and instrumentation limit

The stable origin followed the source-bound four-step sequence below. `A` is
retained-v4 deployment `dpl_B1ZNDHFYWi1ZzS6xT2JqLJcfc1x6`; `B` is illustrated
deployment `dpl_4DfmAArqZN9tug2waKLLostcGNzd`. The existing code-compatible
`Cq` deployment was the starting point. Each receipt verifies the live provider
identity, exact 54-file staged source and three API lambdas, both unchanged
Worker versions and policy, and the prior receipt in the chain. The frozen
operator is `0dec85d01fe09780bda4cbc1edfb7e163f591f0d6b6e4254dd6faa1bfa10bb3d`.

| Phase | Stable transition | Provider verified at, UTC | Receipt SHA-256 |
| --- | --- | --- | --- |
| Prepare | Cq → A | 09:16:36 | `3f398cffd86fd8c7a9f774466e8d77aba9348a8b36d36bd676ab6a31d7b00454` |
| Illustrate | A → B | 09:19:35 | `6bcf96595889673595e0baad91289b075e94dce5c2d54d73107dc7a57854f825` |
| Rollback | B → A | 09:25:16 | `484eaa308f1bac7eeb57edfce312592e88cab25e1dc470ab656b7408f933ce88` |
| Recover | A → B | 09:29:36 | `5417e62cd5799f6d54e3c793f8d546d7be0f46416c4499630e60fcb512dfae20` |

The historical browser report contains 268/268 passing functional assertions:
both voices loaded and played fresh unvisited chapters under the old held
release prefix before reload, exact narrator/audio hash/canonical position/time
and completion survived each reload, and fresh illustrated bookmarks survived
rollback and recovery. All five promotion, fallback, rollback and recovery
flags are true. It observed 76,621,337 native network bytes.

Its overall status remains **failed**, with
`HEADER_VALIDATION_QUIESCENCE_TIMEOUT` during the final
`drain-request-headers-before-close` operation. Closing the contexts then
recorded nine `HEADER_READ_UNAVAILABLE` guard errors. Historical on-wire
request-header shutdown coverage remains incomplete. The report SHA-256 is
`1cb926075d250250d84d6759b02474ddfb6396d49bd62eeb6ad2f97f7a85d939`;
the failed wrapper is
`f92c95a657690bee6fea20f5acc9d9318e96ef35933f07abff8c076c1a9dcf7d`.
These statuses and original bytes are preserved alongside the candidate report,
screenshots and token receipts in the private QA archive. Archive receipt
`f2f5fbdb9853afad4f48463bc9993d0023f44a416dd0c1de33e9b00347444421`
binds all eleven archived artifacts.

The owned preview token was nevertheless revoked, its later request was denied,
and its private handle/configs were removed. Unrelated tokens and deployment
protection were preserved; the global encrypted environment files were
unchanged. Creation receipt
`c51f0be2f245acc583c0294c5050a30f208a0c3f9d991519ff64687bfb7ba0db`
and revocation receipt
`d95e66097d8908e050a9faf89bf2bdd2ecaabbe2a02a730b4525b6fdea4be7f6`
establish cleanup separately from the failed browser result.

## Fresh stable header and browser acceptance

The final stable-only Chrome supplement passed **105/105 assertions**, with no
browser errors. All six contexts closed normally. CDP captured complete on-wire
headers for all 61 Worker requests, including one retained-v4 Worker request;
Cookie, Authorization and Vercel bypass headers were absent on every Worker
hop. No header ledger issues, missing Worker headers or unmatched ExtraInfo
remained. Header values, cookies and media grants were reduced to fixed
booleans and were not recorded. This CDP scope covers page browser requests;
separate APIRequestContext checksum probes are not represented as on-wire
browser header observations.

The run repeated the same five layout/narrator cases, real native playback,
private byte ranges, measured painting reveals, same-recording reload time,
private Cache Storage exclusion, sign-out denial and bounded painting failure
recovery on final stable `B`. It observed 23,851,650 native network bytes. A
separate authenticated browser Fetch requested exactly `bytes=0-1023` through
the old v4 API audio prefix: the real private API redirect and retained Worker
206 response passed the network verdict, with 1,024 response bytes and complete
CDP header evidence. This Fetch check did not alter reader playback or bookmarks
and is separate from the historical old-tab native playback checks.

Final browser report
`c0ad8b103029b460378021d55461ac31a820e39cd2c0a3de5492135cf8084fb8`
(564,595 bytes) finished at 10:02:17 UTC. Successful memory-only wrapper receipt
`87a794a3652d4080d80a71ba898dad05b50debb5909be7739ae667adc778acf5`
records no preview token, bypass, credential config file, global environment
change or provider settings change. The four production transitions were not
repeated. Eleven final report/screenshot/source artifacts are retained under
`operations/qa-archive/2026-10-11-J5dRb3/`, bound by archive receipt
`3e4aad777ec34495690ddc0b47af8519f18222b94b8b8de6ca381deac210c368`.

The final source identities are supplemental harness
`8f605041772724cac16744dbeb018e837c12f5d428186e291cd7da69921f3860`,
CDP header ledger
`840929a7fbb2c1e5158c86fdc53f9e9d9a22e9b7695255c9526e4064fc6f1e18`,
and memory-only runner
`be3de0a9c67f22da346087dc057c7bb0b8ac34c2d7b1d8f16135ccc3f489989e`.
The resulting evidence combines the preserved historical functional rehearsal
and fresh final stable header/closure proof. Historical missing header reads
remain unproven. Final provider/bucket postflight binds this combined evidence as recorded
below; persisted production routing is reconciled as recorded next.

## Final provider postflight

The actual read-only postflight passed at 10:10:57 UTC. The stable alias and
public shell hash bind to illustrated `B`; anonymous book, audio, audio Range
and WebP requests all returned 401 with private cache policy. The R2 bucket
remains private, with managed public access disabled and no public custom
domains. The temporary uploader is absent. Both owned Worker versions and the
current shared-access policy remain exact; Worker logs are disabled. Account
usage settings match the preflight snapshot; a subscription-state comparison
is not claimed, and no paid Worker upgrade was made. Preview protection,
unrelated testing bypasses and owned QA-token cleanup all passed.

Receipt `72755e9360047e5440a150bbf405d6b57024f6e9e0cf342baf24bfa85a5f4772`
(5,132 bytes) binds the four provider transitions, preserved failed historical
functional report, candidate acceptance, final stable supplement and its
complete CDP header proof under contract
`preserved-functional-rehearsal-plus-fresh-stable-CDP-v1`. It explicitly records
historical header coverage as incomplete and makes no retroactive header claim.
The frozen operator is
`63382fa3ab70a8bafffba0d93d7d68fa2ed10063503a2b9de36c231ff99189b7`.
It made no provider writes or global encrypted environment changes.

## Persisted production routing

Production defaults now select `lumen-media-illustrated-v1` for current sessions,
book data and new-release assets, with `LUMEN_PREVIOUS_RELEASE_ID` set to
`lumen-private-192-v4` and `LUMEN_PREVIOUS_WORKER_ORIGIN` set to the retained
`lumen-media` origin. The sensitive production-origin row keeps its type;
the five unrelated rows, including the separate preview row, remain unchanged. Two complete
fresh GET snapshots agree across all eight rows, and protection, both Workers
and the current policy remain exact. The encrypted local environments remain
the retained-v4 operator context.

The first mutation attempt failed before its origin PATCH; its two owned rows were
removed by verified cleanup. A retry accepted the three intended changes, but
the frozen mutator's readback guard rejected the GET-only `decrypted` annotation.
Those failed mutator receipts and the owned cleanup remain preserved. A separate
read-only reconciliation matched the accepted ACK to the current opaque row,
omitting only `decrypted:false` and the three documented GET annotations for
ACK comparison while guarding the complete raw GET state. It made no additional
provider writes or local environment changes; sensitive-origin plaintext
readback is not claimed.

Success was observed at 10:53:23 UTC. Reconciled receipt
`223e0e2365a69b9e7baf2320eedcf412b8004329852f5ade66d77a35d7323461`
(2,853 bytes) binds source
`4fc6f923acb3b15a7792838b6a5cab8b14ef09be7aec2cd1dd38f4c60ce82301`,
the frozen mutator
`8095ca82213ec4370789dde0d16bbfc57d66f6de9f9afb74de3b7bd75940904d`
and its unchanged failed receipt
`a7f0a71bfbea0313c7465738f82b21031a1a4f43da782c343382135bccb83ca5`.
The receipt directory timestamp records operation start, not exact completion.

## Sampled Worker CPU evidence

The read-only 09:27–09:33 UTC window contains 37 aggregate groups, 93 requests
and zero reported errors, with no possible truncation. Its highest reported
group P99 CPU value is 58,658 microseconds (58.658 ms). This is a sampled group
quantile, not a global P99 or exact per-request CPU measurement. CPU spikes
remain an operating follow-up; no paid upgrade or settings change was made.

Window receipt
`04d0dd8c5f79bf2074ca1a52b303c183e4b8cb3816f6b0290b717e2f28f11e0f`
keeps the raw provider values and records its own unit as unknown without a
conversion. The separate primary-schema description receipt
`9ea80c76839c49f33dd93ca49d59837204d550d1ce7291400ef4a2db943b8147`
explicitly identifies P50/P99 units as microseconds; that receipt supports the
conversion shown above.

## Open-reader continuity and operator context

The illustrated Vercel stage selects the new Worker for `/api/book`, sessions
and new-release assets. Its paired previous-release settings route only
`/api/assets/lumen-private-192-v4/` to the retained Worker. The retained v4 stage
reverses that pairing so still-open illustrated tabs can request their existing
release during a rollback. These static server-only routes preserve exact
Worker-origin redirect validation; they are not client-selected destinations.
Both Workers retain the current access policy. The historical functional
rehearsal verified before-reload navigation, both narrator bookmarks, canonical
position, exact time and completion during promotion, rollback and recovery;
its final header-read shutdown limit is recorded above.

The global encrypted local `.env`/`.env.shared` pair remains the retained v4
operator context. The reviewed private illustrated-media operator pins the new
Worker name, source/build and index identities explicitly, preventing a generic
default-Worker deployment from selecting the illustrated release accidentally.
Do not infer a deployed release pointer from those retained local files.
Future operations must use the matching source-bound operator and provider
readback; never restore an obsolete registry that re-enables revoked access.

Private artwork, chapter JSON and audio continue to bypass service-worker Cache
Storage. The reader loads the selected active painting and the next one,
preserves the same-recording bookmark, and leaves narration in the native
network stack. Shell updates wait for existing tabs to close. Physical-device
installation/headset/background acceptance, full human listening, and individual
human onset review remain separate from automated release evidence.

The source-bound upload, hosted acceptance, cross-release rehearsal, final
provider postflight and persisted routing evidence are recorded in
[deployment.json](../../web/data/deployment.json). Physical-device acceptance,
human whole-book listening/onset review and operating-budget follow-ups retain
the limits stated above.
