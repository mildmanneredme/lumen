# Private reader delivery

Only the empty reader shell is static. The selected full-book release uses **private Cloudflare R2** and a Worker gateway. Vercel sends small session/book/JSON requests over a secret-authenticated bridge and redirects audio to the Worker. The Worker owns the invite registry, sessions, signed media grants and immutable index. See [Cloudflare delivery](../../docs/backlog/cloudflare-delivery.md) for the reviewed build/upload procedure and current hosted evidence.

The previous private Blob implementation remains a supported fallback. Its configuration and uploader are documented separately below; no Blob store was provisioned for this release.

`api/session.js`, `api/book.js`, and `api/assets/[...path].js` use the shared
Node adapter. `LUMEN_WORKER_ORIGIN` selects the Cloudflare bridge; without it, the adapter uses the server-only, pinned `@vercel/blob` fallback SDK. The existing Vercel
project uses Node 24. Run `npm ci` inside `web/` and
`node --test web/tests/private-access*.cjs` from the repository root.

## Reader API

- `GET /api/session`: `{authenticated:true|false}`.
- `POST /api/session`: JSON `{invite:"<shared access code or legacy token>"}` and the exact
  app `Origin`; returns `{authenticated:true}` with a signed cookie.
- `DELETE /api/session`: exact app `Origin`; expires the cookie and returns
  `{authenticated:false}`.
- `GET /api/book`: the indexed book manifest, after authorization.
- `GET|HEAD /api/assets/<exact inventory path>`: an indexed immutable JSON,
  MP3, or WebP object, after authorization.

The shared access code contains six short words. Uppercase letters, spaces and dashes normalize to a lowercase, hyphen-separated code before hashing. The server tries an exact legacy 256-bit base64url token first, preserving its case and bytes. Legacy invitation links use URL fragments, which the shell removes before exchanging them. Query credentials are rejected. The cookie is `__Host-lumen_session`, `Secure`,
`HttpOnly`, `SameSite=Lax`, and scoped to `/`; it expires after 30 days or the
invite expiry, whichever comes first. Every request rechecks invite revocation,
expiry, and version. Revoking/removing a grant or incrementing its version invalidates its sessions after the updated server configuration is deployed. Logout removes the current browser cookie.

Responses use `private, no-store`. Unauthenticated requests fail before reading the private index or requesting media. HEAD and conditional requests authenticate too. In Cloudflare mode audio receives a 307 redirect with a separately signed path/hash/release/invite/expiry grant. The Worker validates that grant and its current invite policy before streaming. JSON remains behind the Vercel bridge. The service worker bypasses private API and Worker media requests, including Range requests.

## Selected Cloudflare environment

Vercel receives only `LUMEN_APP_ORIGIN`, `LUMEN_WORKER_ORIGIN` and `LUMEN_BRIDGE_KEY`. The Worker receives the sole session/invite configuration, immutable R2 index pointer/hash, separate media signing secret, exact allowed origins and private R2 binding. The temporary uploader uses a separate secret and is deleted after integrity verification. Keep all secret values in the machine’s encrypted ignored dotenvx files and transfer only declared bindings privately; the owner’s plaintext code never enters either deployed service. Rotations/revocations require deploying the updated Worker policy.

Private receipts currently prove the complete 365-asset R2 upload and uploader cleanup. The Vercel candidate remains protected; hosted browser verification and production promotion are pending.

## Legacy Blob server environment

The following values apply only when the Blob fallback is selected. Server-only values are supplied to those Vercel Functions. Locally use
the machine's dotenvx wrapper from `web/`, with encrypted ignored owner-only `.env` and `.env.shared` files and a separate owner-only `.env.keys`; never put these values in frontend build
variables, JavaScript, static JSON, source-control commits, or command arguments.

Required names:

- `LUMEN_APP_ORIGIN`: `https://lumen-phi-five.vercel.app`, with no trailing slash.
- `LUMEN_SESSION_SECRET`: 32–64 random bytes encoded as canonical base64url.
- `LUMEN_INVITES_JSON`: at most 50 grants. Each is `{id,tokenHash,version,
  expiresAt,revoked}`. `tokenHash` is SHA-256 of the canonical lowercase six-word code joined with hyphens, or the exact legacy base64url token string. `expiresAt` is Unix seconds; `version` starts at 1; `revoked` is boolean. The shared audience uses one grant; this is not a per-person or per-device count limit. Original codes/tokens remain in encrypted private operator records only.
- `LUMEN_RELEASE_INDEX_PATH`: immutable
  `lumen/releases/<releaseId>/server-index.<sha256>.json`.
- `LUMEN_RELEASE_INDEX_SHA256`: that file's exact byte hash.
- Blob credentials: prefer a project-connected `BLOB_STORE_ID` and automatic
  Vercel OIDC. The SDK also accepts server-only `BLOB_READ_WRITE_TOKEN` where
  OIDC is unavailable. Never read or pass the automatic OIDC token manually.

`LUMEN_OWNER_ACCESS_CODE` is an operator-only local record encrypted in the ignored environments. Never deploy it or automatically import all local variables into Vercel. Deploy only the required server configuration above.

The inert operator library [shared_access_code.cjs](../scripts/shared_access_code.cjs) returns `{code,invite}` only when explicitly called with a future expiry. It selects six independent words using `crypto.randomInt` from 1,295 original entries of the pinned EFF short wordlist; the original file and attribution are preserved. Capture the code in private operator memory, encrypt the owner record, and deploy only the grant hash. Importing or directly running the library generates no credentials or output.

To rotate the shared code, generate a fresh code/hash and increment the same grant's version; to close access, mark the grant revoked. Update the encrypted server configuration and redeploy. Existing cookies then fail the grant checks. Signing out expires only that device's cookie and preserves its local reading position.

No ignored environment file is required inside a deployed function. The
provider injects its secure server environment at runtime.

## Release index contract

Set both release export bases to
`https://lumen-phi-five.vercel.app/api/assets/`. A server index has
`{schemaVersion:1,releaseId,appOrigin,manifestPath,assets}`. Each asset is
`{path,blobPath,sha256,bytes,contentType}`; paths are exact, structured inventory
paths with content-hash filenames. `manifestPath` selects an indexed JSON file.
The server validates the entire index, verifies its configured physical hash,
and retains only that immutable index in memory.

`buildServerIndex(uploadInventory,privateUploadReceipts,manifestURL)` reconciles
every receipt with the export's byte count, hash, type, and same-origin logical
URL. Receipts must declare `access:"private"`; duplicates, missing files, public
inventories, URL proxies, and unsafe paths fail. Local source paths and private
reviewer information are excluded from the resulting index. Remote byte/range
verification remains a promotion prerequisite, performed before binding the
index in the production environment.

## Legacy Blob upload and verification

The operator [upload_private_release.cjs](../scripts/upload_private_release.cjs)
is excluded from the deployment. It accepts the exporter inventory and an
explicit owned root, private output directory, manifest URL, upload byte ceiling,
and remote verification byte ceiling. It finishes plan, source size, all three
source samples, and complete source SHA-256 checks before the first upload.
Streams are physically hash-bound during upload as well. Every Blob request uses
`access:"private"`, immutable names, no random suffix, and no overwrite.

Each upload is downloaded deliberately for complete SHA-256 verification and
three exact byte windows before the immutable private server index is uploaded.
An interrupted run can be repeated: existing objects are completely verified
and reused, while incorrect existing objects fail without overwriting. Receipts
are written as owner-readable files under ignored `Audiobook/author-audit/`.
Source paths, cookies, invite tokens, and manuscript contents are absent from
operator console output. The operator does not update a production pointer.

Run from `web/` through the local dotenvx wrapper, substituting real private
paths and explicit byte ceilings:

```sh
dotenvx run -f .env.shared -- node scripts/upload_private_release.cjs \
  --root /absolute/path/to/Novel-proj \
  --inventory /absolute/private/upload-inventory.json \
  --manifest-url https://lumen-phi-five.vercel.app/api/assets/tracks/chapter-000/book-manifest.HASH.json \
  --out /absolute/path/to/Novel-proj/Audiobook/author-audit/private-release \
  --max-upload-bytes UPLOAD_CEILING --verify-byte-budget READ_CEILING
```

After a candidate deployment, obtain an operator session privately through the
normal invite exchange. Provide its cookie through the server-only encrypted
environment as `LUMEN_OPERATOR_SESSION_COOKIE`, then run the same command with
`--verify-api --api-byte-budget READ_CEILING`. This profile checks anonymous
manifest, asset HEAD and asset Range denial, foreign-Origin cookie denial,
authorized HEAD, three exact Range sample hashes, and unsatisfiable `416`.
It requires private/no-store and same-origin resource policy; it does not
assume public CORS or anonymous delivery. Its report records access verification
separately from the uploader's deliberate complete physical hash proof.

## Legacy Blob audio seeking

Audio streams through the native browser network stack. Single byte ranges,
open ranges, and suffix ranges return exact `206` bytes and `Content-Range`;
unsatisfiable or unsupported ranges return `416`. HEAD returns trusted release
metadata without downloading audio. Stale `If-Range` requests receive the whole
object. No complete audio file is buffered by the function.

The pinned SDK exposes successful upstream ranged responses as `statusCode:200`
even when the upstream status was 206. The server verifies the original
`Content-Range` and `Content-Length` headers against the approved inventory;
ignored or mismatched ranges fail closed. Complete JSON files are buffered
within a 2 MiB bound and physically hashed before any prose is returned.

The installed Vercel CLI 48.6.0 cannot create private stores. Use a temporary
pinned CLI version >=50.20.0 and explicit `--access private` after spending is
authorized; do not use an existing public store. Keep the existing project and
production origin stable.

Official references: [private Blob delivery](https://vercel.com/docs/vercel-blob/private-storage),
[SDK streaming and credentials](https://vercel.com/docs/vercel-blob/using-blob-sdk),
and [Blob CLI commands](https://vercel.com/docs/cli/blob).
