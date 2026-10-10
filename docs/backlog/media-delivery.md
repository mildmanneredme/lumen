# Lumen media delivery and release operations

Updated 10 October 2026. The author selected Cloudflare and enabled R2. All 365 immutable release assets and their server index are now uploaded to private R2 storage and independently verified by full SHA-256 reads and 1,095 sample ranges. The temporary uploader has been removed. The protected Vercel candidate is built; hosted browser verification and stable-origin promotion remain pending. Character/artwork review continues separately with generic placeholders in this release.

## Implemented delivery proof

The selected launch uses private R2 and a Cloudflare Worker behind the stable Vercel reader API. The Worker owns access policy and sessions. Vercel forwards small JSON requests over an authenticated bridge and redirects authorized audio to the Worker; MP3 bodies stay out of Vercel Functions. [Cloudflare delivery](cloudflare-delivery.md) records secret ownership, signed media grants, range streaming, actual upload receipts and promotion gates. Responses use `private, no-store`; the service worker bypasses private requests. The reviewed integration passed 118 backend/operator/deployment checks and 27 actual workerd HTTP checks. Live gateway authorization passed 39 checks, including all 182 audio HEAD/grant bindings and a complete largest-chapter stream. Direct native Chrome audio also passed 18 checks in both voices, including cold seeking, ±15 seconds and resume. Those proofs remain separate from complete R2 object integrity, Vercel app/browser acceptance and physical-device testing. The older private Blob implementation remains an optional fallback; no Blob store was provisioned.

The Python [verify_delivery.py](../../web/scripts/verify_delivery.py) is the separate **public-media profile** retained for future public-delivery choices. Its input inventory is an operator-private file; the URLs it tests must allow anonymous public access. It performs a HEAD, three GET ranges, and an unsatisfiable range for each asset. The beginning/middle/end bodies must match independently computed source sample SHA-256 values, the exact inclusive `Content-Range`, total file size, and `Content-Length`. Wrong bytes or file totals, compressed range bodies, missing CORS, unexpected redirects, and inadequate cache policies fail the check. An ETag is recorded as a provider validator; it is never treated as a SHA-256. Do not use this public profile to certify the selected private release.

All origins need an independent command-line allowlist; a hostname in the inventory does not grant permission to fetch it. Remote hosts require HTTPS. Loopback fixtures require both an allowlisted origin and `--allow-loopback`. Redirects are rejected before following them. Requests carry the reader Origin, request identity encoding, and send no cookies or Authorization. The verifier has no HTTP cache and disables ambient proxy configuration. Reports omit URL queries and local `sourcePath` values.

The default read budget is **1 MiB for the entire inventory**. Each sample is at most 1,024 bytes, so testing the 182 current audio files reads approximately **559 KB**, plus no HEAD or error bodies. The preflight reserves one extra byte per read to detect overlong responses and rejects insufficient total budgets before making any request. Whole-file download/hashing requires both `--full-sha256` and an explicit adequate `--byte-budget`. A 2.66 GB full-edition verification is therefore an intentional operation rather than a side effect of a basic health check.

The report distinguishes `sampleSha256Verified` and `fullSha256Verified`. Passing the bounded checks proves streaming/sample integrity; it does **not** establish that every byte of an object matches the complete release hash. Full checksum evidence from a deliberate full read or a verified provider checksum must be recorded before publication. `accessPolicyVerified` remains false: successful playback cannot establish authorization, entitlement, or human content approval.

Eighteen tests exercise tiny local HTTP fixtures, including thirteen malformed response scenarios, signed-query redaction, redirect refusal, explicit allowlisting, missing sample proofs, full hashing, and whole-inventory budget preflight. The header regressions require exactly one valid CORS allow-origin value, combine every Cache-Control line, reject private/no-store/no-cache directives including parameterized forms, and reject conflicting max-age values. Compatible split cache headers retain their combined evidence. The fixtures upload nothing and do not read production audio.

```sh
python3 -m unittest discover -s web/tests -p test_verify_delivery.py
```

## Retained public-verifier inventory contract

Keep the upload inventory with private release artifacts outside `web/dist`. It is produced by the release exporter and used by a separately authorized uploader/verifier. Public manifests must omit local paths, authoring references, credentials, and signed links intended only for an audit.

```json
{
  "schemaVersion": 1,
  "releaseId": "lumen-v6-example",
  "accessModel": "public",
  "appOrigin": "https://lumen-phi-five.vercel.app",
  "mediaOrigins": ["https://media.example.com", "https://lumen-phi-five.vercel.app"],
  "assets": [
    {
      "url": "https://media.example.com/lumen/RELEASE/VOICE/TRACK/audio.SHA256.mp3",
      "sha256": "<64 lowercase hexadecimal characters from the exact local file>",
      "bytes": 1048576,
      "contentType": "audio/mpeg",
      "immutable": true,
      "sourcePath": "<private local path for the uploader only>",
      "samples": [
        {"start": 0, "end": 1023, "sha256": "<source range SHA-256>"},
        {"start": 523776, "end": 524799, "sha256": "<source range SHA-256>"},
        {"start": 1047552, "end": 1048575, "sha256": "<source range SHA-256>"}
      ]
    }
  ]
}
```

This is an illustrative schema; replace placeholders with exporter-generated hashes and URLs. For an asset of `n` bytes, use width `min(1024, n)` and offsets `0`, `(n-width)//2`, and `n-width`, in that order. End offsets are inclusive. Small objects may have repeated windows. `audio/mpeg`, `image/webp`, and `application/json` are supported. Include the app origin when its immutable chapter JSON is part of the inventory. Sample hashes come from the exact approved encoded file or immutable JSON bytes; they are not guessed from URLs or remote headers.

The current verifier profile requires anonymous CORS-readable delivery and a public `max-age` of at least one day. Set a year for content-hashed assets; an `immutable` directive is recommended. Mutable release pointers must revalidate and are checked separately. Private delivery needs an additional authorization/expiry profile, because cache and access policies differ. Vercel signed GET and HEAD URLs are scoped to different operations; the single-URL profile is not a complete private-Blob verifier. Signed URLs can deliver private files without proxying their bytes through a function, but the application must issue appropriately scoped links and preserve seeking across renewal. [Vercel Signed URLs](https://vercel.com/docs/vercel-blob/vercel-signed-urls).

After approved objects have been uploaded, a bounded check is:

```sh
python3 web/scripts/verify_delivery.py Audiobook/author-audit/release/upload-inventory.json \
  --allow-origin https://media.example.com \
  --allow-origin https://lumen-phi-five.vercel.app \
  > Audiobook/author-audit/release/delivery-check.json
```

For a deliberately approved complete checksum pass, append `--full-sha256 --byte-budget <approved-bytes>`. The budget must cover every listed object plus sample reads and extra-byte reservations. Exit codes are 0 for passed delivery, 1 for a failed delivery check, and 2 for invalid inventory/options. Store reports privately even though signed query parameters are redacted.

## Measured capacity and current provider choices

The source inventory contains **182 MP3s / 2,657,554,540 bytes**. Autonoe is 1,334,133,566 bytes and Charon is 1,323,420,974 bytes. One listener generally completes one edition, averaging **1.3288 decimal GB**. The largest chapter is **35,239,853 bytes**. Metadata/file sizes were read without repeating the already completed 2.66 GB hash verification.

Two retained audio releases occupy 5.3151 GB; three occupy 7.9727 GB. Art, text, timings, alternate encodes, backups, and download packages add to storage. The two existing M4Bs are about 0.94 GB each; their ZIPs are about 1.33 GB each. Chapters fit both providers' ordinary cache size limits. Entire M4B/ZIP products exceed those limits and need separate measured download delivery. Vercel Blob's cache ceiling is 512 MB; Cloudflare Free/Pro/Business cache ceiling is also 512 MB. [Blob limits](https://vercel.com/docs/vercel-blob/usage-and-pricing), [Cloudflare cache limits](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/).

**Recorded choice:** private R2 Standard storage with a Workers Free gateway, the stable Vercel app origin and approximately 50 invited readers. The verified release occupies 2,667,632,125 bytes including its index. The account had no existing R2 storage during preflight. No paid Workers upgrade was performed. The following public-provider cost comparison is retained as earlier planning evidence; it does not measure this private Worker gateway. R2 storage/operation overages remain billable and the account spending ceiling is still an author decision.

R2 Standard currently includes 10 GB-month, one million Class A operations, and ten million Class B operations monthly. Above those allowances it charges $0.015/GB-month, $4.50/million Class A, and $0.36/million Class B; direct Internet egress is free. Billing rounds up units, and these allowances apply to the account, not solely Lumen. Use Standard rather than Infrequent Access for actively played audio. [R2 pricing](https://developers.cloudflare.com/r2/pricing/).

Vercel Blob's Hobby allowance is 1 GB storage and 10 GB transfer, insufficient for this complete audio library. Exceeding Hobby allowances can interrupt Blob availability. Current Pro storage/operations use credits and on-demand billing; do not assume the older 5 GB/100 GB Pro allowances. Public delivery also involves CDN requests and origin transfer on cache misses. [Blob pricing](https://vercel.com/docs/vercel-blob/usage-and-pricing).

Vercel Flat Rate CDN includes Blob transfer among covered metrics, but its eligibility excludes large-scale media/file delivery when that is the majority of bandwidth. Audiobook hosting is likely to meet that description; **this is an inference**, requiring provider confirmation. Do not budget the audiobook against a 1 TB or 50 TB flat-rate entitlement unless Vercel confirms eligibility for this workload. [Flat Rate CDN eligibility](https://vercel.com/docs/pricing/flat-rate-cdn).

### Illustrative monthly audio transfer

These figures use one average complete edition per completion equivalent and exclude repeated listening, seeks that redownload bytes, voice auditions, images, app delivery, tax, subscription/domain charges, and packages. Blob figures below are gross on-demand **Blob Data Transfer only**, before applicable credits, cache-miss origin charges, storage, or request charges. Prices vary by region. Singapore currently lists $0.053/GB Blob transfer and $0.27/GB origin transfer; use the actual store/client-region mix at provisioning. [Singapore pricing](https://vercel.com/docs/pricing/regional-pricing/sin1), [regional ranges](https://vercel.com/docs/pricing/regional-pricing).

| Complete-listen equivalents/month | Audio GB | Blob BDT at $0.050/GB | Blob BDT at Singapore $0.053/GB | Direct R2 egress |
| ---: | ---: | ---: | ---: | ---: |
| 25 | 33.219 | $1.66 | $1.76 | $0 |
| 100 | 132.878 | $6.64 | $7.04 | $0 |
| 1,000 | 1,328.777 | $66.44 | $70.43 | $0 |
| 10,000 | 13,287.773 | $664.39 | $704.25 | $0 |

At an illustrative **30% byte-weighted cache miss ratio**, Singapore origin-transfer costs would add about $2.69 / $10.76 / $107.63 / $1,076.31 to those rows. This is a sensitivity calculation, not a measured prediction; request hit rate does not directly equal byte hit rate. Storing both editions at Singapore's $0.025/GB-month is approximately $0.066/month before credits. Other Vercel resources and account plan fees remain additional. [Singapore rates](https://vercel.com/docs/pricing/regional-pricing/sin1).

For R2, an illustrative 20 origin reads per chapter produces 1,820 reads per full listen: 45,500 / 182,000 / 1.82 million / 18.2 million in the four rows. With otherwise-unused free allowances, only the last exceeds ten million; its rounded additional read charge is approximately $3.24. This assumption must be replaced with observed origin operations. Cloudflare can expand a small client range to 1 MiB boundaries and issue several origin requests, so browser request counts alone cannot price R2 reads. [Range behavior](https://developers.cloudflare.com/cache/reference/range-requests/), [R2 billing](https://developers.cloudflare.com/r2/pricing/).

## Account evidence and remaining decisions

Read-only CLI checks on 10 October confirmed Vercel CLI **48.6.0**, project `lumen`, project ID `prj_g2eoQPikqSKEEEouyT3FBTuEfACs`, and scope `robert-xies-projects`. The stable app origin remains `https://lumen-phi-five.vercel.app/`. [vercel.json](../../web/vercel.json) sets `outputDirectory: dist`; deploy from `web/` so that local configuration applies. Remote project inspection reports default framework settings, so it does not by itself prove the effective local deployment output. Existing release evidence is in [deployment.json](../../web/data/deployment.json).

The original installed Vercel CLI was 48.6.0; candidate deployment and environment operations now use pinned CLI 63.1.2. Cloudflare CLI authentication, R2 activation, a private APAC Standard bucket and the Workers gateway were verified. Original media hashes and stable logical asset URLs are unchanged. Worker observability and Logpush are disabled to avoid recording signed media credentials. Local operator environments follow the encrypted dotenvx convention. A complete Vercel browser proof remains pending the author’s decision on a temporary deployment-protection testing token; deployment protection remains enabled.

- [x] Author selected a private shared-code release of current audio, faithful prose and placeholder artwork for approximately 50 readers.
- [ ] Record the monthly spending ceiling and account alert/limit response. Cloudflare, the audience and owner Rob Xie are already selected; Workers stays Free unless separately authorized.
- [x] Author accepted both unchanged 192 kbps editions as-is. Source-bound hashes and technical timing verification passed; unavailable cues remain explicit and no full human timing review is claimed.
- [x] Verified R2 entitlement, Standard allowances, private bucket policy, encrypted server configuration and a separate temporary upload secret; uploader removed after complete integrity verification.
- [x] Provisioned the authorized Cloudflare account and Worker hostname. R2 public domains remain disabled; the stable reader origin is unchanged.
- [ ] Complete hosted browser verification and coherent rollback evidence before stable-origin publication. Session/invite/grant authorization and private-cache rules are implemented and reviewed; physical Android acceptance remains separate.

The completed provisioning does not establish a completed launch. Keep the pilot in production until the candidate’s hosted checks pass; retain private release receipts and the immutable index for rollback.

## Retained public-provider provisioning checklist

The following provider/domain/cache instructions are historical planning for future public media. They do not apply to the selected private Worker gateway, which uses no-store responses and leaves all R2 public domains disabled. Use [Cloudflare delivery](cloudflare-delivery.md) for the current deployment and verification procedure.

For **R2 Standard**, create the selected access policy and least-privilege upload access; connect a production custom domain in the same Cloudflare account, and configure cache behavior before a preview. `r2.dev` is rate-limited development access and does not provide the production caching/security setup. Keep it disabled when custom-domain access is intended to be protected. [R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/).

For **Vercel Blob**, create the chosen store in the agreed region, confirm its project binding and billing plan, and upload approved content-hashed paths without overwriting. Configure cache duration explicitly. Public Blob can serve range requests and custom cache duration; each exact encoded file remains the timing authority. [Blob public caching](https://vercel.com/docs/vercel-blob/public-storage), [Blob ranges](https://vercel.com/docs/vercel-blob/examples).

For either provider:

1. Configure GET/HEAD CORS for the stable app origin and controlled previews, allow needed Range requests, and expose `Content-Range`, `Accept-Ranges`, and `ETag`. Include Origin in command-line probes. If changing R2 CORS on previously cached objects, refresh the cached headers as directed by the provider. [R2 CORS](https://developers.cloudflare.com/r2/buckets/cors/).
2. Upload only the private inventory's approved files with exact MIME types and immutable keys. Keep originals and earlier published assets. Do not upload raw clips, lossless masters, author audit notes, casting sheets, or credentials as part of reader delivery.
3. Run the bounded verifier against the real host twice and compare available `cacheStatus`/`age` evidence. A pass does not prove that either run was cold: capture known cold/warm states in the provider's metrics without mutating an existing audio URL.
4. Record complete checksums for every object and the uploader's source inventory hash. A successful upload and an ETag do not replace that evidence.
5. In an HTTPS preview, play both voices, seek into an unbuffered beginning/middle/tail, pause/reopen, simulate connection loss, and retry at the saved passage. Keep the native audio stack and service-worker audio/Range bypass.
6. Verify actual Android Chrome installation, lock-screen/media controls, Bluetooth/headset input, interrupted playback, chapter transitions, and returning-listener resume on the physical phone. Automated layout/HTTP checks are separate evidence.

Cloudflare's current range path requires coherent total size, exact unencoded partial responses, and matching interval lengths. A tiny requested range does not bypass the complete object's cache size limit. The fixture suite checks the corresponding HTTP behavior; only real-host tests establish the provider configuration. [Cloudflare range requirements](https://developers.cloudflare.com/cache/reference/range-requests/).

## Preview, promotion, and rollback checklist

1. Save a release record containing source commit, immutable book/data manifest hashes, upload inventory hash, full media verification evidence, migration test evidence, assigned owner, and previous app/release/deployment identifiers. Review all required approvals and promised coverage.
2. Upload and verify approved media **before** exposing a manifest. Deploy the coherent app/data release to a Vercel preview using the existing project and local `web/vercel.json`. No author-only artifacts may enter `dist`.
3. Prove saved pilot migration and the intended release on preview, then verify the unchanged stable origin's alias, PWA files, first/last tracks, narrator switching, and completion/resume in the production candidate.
4. Publish/promote the app and release pointer coherently. Keep listening tabs on their loaded release; retain previous immutable files and service-worker caches until their references can age out. Do not replace audio bytes behind an established URL or force an active listener to reload.
5. Watch playback/seek failures, media 404s, resume failures, Worker/Vercel errors, R2 operation counts, Worker request/CPU allowances and account spend for the first 48 hours. Use sanitized paths/statuses rather than signed media queries. Assign actionable thresholds before launch; service limits and media usage must be measured separately.
6. For a blocking regression, restore the recorded working app **and its coherent manifest pointer**, retain all valid audio assets, and verify stable-origin resume without clearing local progress. Vercel rollback restores app deployment/aliases, not an independently modified external manifest or object store.

The installed CLI supports `vercel rollback <deployment-id-or-url> --scope robert-xies-projects` from `web/`. The currently recorded pilot deployment is `dpl_EeReRPLBLBjyRgXwNoLhVULuhKjG`; record the actual immediate predecessor when publishing a later release. Hobby rollback eligibility is limited to the previous production deployment. Rollback disables automatic production alias assignment until another deployment is promoted; verify alias state and restore normal promotion behavior deliberately. [Vercel rollback](https://vercel.com/docs/instant-rollback).

Rollback has been **prepared, not executed**. Private R2 provisioning, complete release upload, direct live gateway checks and representative native Cloudflare audio checks are complete. Vercel app/browser acceptance, coherent rollback rehearsal, stable-origin promotion, physical-phone acceptance, account spending ceiling and repository privacy decision remain pending.
