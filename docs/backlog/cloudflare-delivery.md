# Private Cloudflare delivery

The author selected Cloudflare R2 on 10 October 2026 and enabled its account billing. Lumen keeps its existing Vercel reader address and immutable `/api/assets/` paths. Both original 192 kbps editions, canonical text, timing maps and recording hashes are unchanged.

## Access and delivery

Vercel forwards small session and JSON requests to the `lumen-media` Worker over a server-authenticated bridge. The Worker owns the session signing key and the hash-only invite registry. Vercel needs only `LUMEN_APP_ORIGIN`, `LUMEN_WORKER_ORIGIN` and `LUMEN_BRIDGE_KEY`. The plaintext owner access code is never deployed.

Audio and future paintings receive a private, uncached 307 redirect to Cloudflare. The redirect contains a separately signed grant scoped to the asset path, hash, release, reader origin, invite ID/version and current session expiry. It contains no access code or original cookie. The Worker rechecks its current invite policy on every media request, including HEAD, conditional and Range requests. Rotate or revoke the Worker registry with a 100% deployment to deny subsequent requests. Signing out clears the local cookie and active media while preserving bookmarks; it does not retract bytes already delivered.

The `lumen-private` R2 bucket uses Standard storage in APAC. Its managed `r2.dev` access stays disabled and it has no public custom domain. The Worker streams exact closed, open and suffix ranges with the correct length and status. Workerd needs `FixedLengthStream` to retain streamed response lengths. Full native R2 reads may include an explicit full-object range; the adapter accepts that exact interval.

Private responses use `private, no-store`. Cross-origin media permits the grant's exact reader origin and the `null` origin produced by a redirect; the signed grant remains required. CORS preflight checks access too. The reader service worker bypasses private API and Cloudflare media requests. No private offline cache is introduced.

Signed media URLs are credentials. Worker observability and Logpush are disabled; query redaction is also configured. Do not record complete media URLs, cookies, request bodies, environment values or passages in troubleshooting logs.

## Build and upload

The isolated Worker project is `web/cloudflare/`, with pinned official `cf` and Wrangler build tools. Vercel excludes this directory from its upload. From that directory, install its lockfile dependencies, then run `npm run build`. `cf deploy --dry-run --prebuilt` checks the resulting Build Output without uploading it. Run `cf build --mode upload` only for the temporary upload Worker.

The reviewed configuration declares secret bindings. Supply only the declared values from the encrypted, owner-only `web/.env` through dotenvx at deployment time. Prefer an in-memory API multipart body or a private pipe to `cf deploy --secrets-file /dev/stdin`; never put decrypted values in shell arguments or committed configuration. Deploy with resource auto-provisioning disabled because the private bucket is already explicitly configured. Local `.env.shared` and `.env.keys` retain the machine's dotenvx convention.

`upload_r2_release.cjs` exports a strict `uploadR2Release` engine. `r2-worker-adapter.cjs` supplies its authenticated HTTP adapter. The temporary `lumen-upload` Worker has a separate secret and accepts only immutable, conditional writes under `lumen/releases/lumen-private-192-v4/`. R2 validates each write's SHA-256 checksum before committing it. No permanent S3 credentials are required.

Before any remote operation, the engine verifies every owned source, file identity, SHA-256 and three sample ranges against the exact prepared inventory. It uploads without overwriting, independently reads and hashes every remote object, verifies all 1,095 sample ranges and final object identities, then publishes the immutable index last. Explicit upload and verification byte budgets are required. Resuming an interrupted upload re-verifies existing bytes. Delete only the temporary upload Worker after verification; retain immutable release objects for rollback.

The prepared inventory has 365 objects totaling 2,667,475,488 bytes, plus a 156,637-byte server index. The largest MP3 is 35,239,853 bytes. It fits the current R2 free storage allowance and the free Worker's upload-body limit. The account had no existing R2 storage during preflight. R2 has no Internet egress charge; storage and operations beyond its account-wide free allowances remain usage-based. Workers remains on its free plan. Its CPU and daily request limits must be checked against actual hosted playback before promotion. [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/).

## Promotion and rollback

Retain private receipts under `Audiobook/author-audit/web-release/` for the bucket privacy check, reviewed source commit, built module hash, Worker version, encrypted-environment preparation, upload verification and hosted API/browser evidence. Local tests and provider creation alone do not establish a completed launch.

Deploy a Vercel preview with the three bridge settings and allow its exact platform-provided origin on the Worker. Keep Vercel deployment protection enabled while testing. Verify real access denial, JSON hashes, authenticated HEAD, all sample ranges, conditional requests, a complete large MP3 transfer, native Chrome playback/seek/resume, narrator switching and phone/iPad/desktop layouts. Check that protected data and grants never enter Cache Storage.

Promote the reviewed candidate to the stable Vercel project only after hosted checks pass. Record the previous pilot deployment before promotion. A rollback restores the matching Vercel deployment, bridge settings and Worker index/policy version; it leaves media and browser bookmarks intact. Rehearse that coherent switch on the candidate first. Character artwork and actual physical-device installation acceptance remain separate reviews.
