# Private reader delivery

Only the empty reader shell is static. The complete manuscript, timing maps,
recordings, and future paintings belong to a **private** Vercel Blob store.
No client Blob tokens, unauthenticated upload route, filesystem route, or
arbitrary URL proxy is provided.

`api/session.js`, `api/book.js`, and `api/assets/[...path].js` use the shared
Node adapter and a server-only, pinned `@vercel/blob` SDK. The existing Vercel
project uses Node 24. Run `npm ci` inside `web/` and
`node --test web/tests/private-access*.cjs` from the repository root.

## Reader API

- `GET /api/session`: `{authenticated:true|false}`.
- `POST /api/session`: JSON `{invite:"<256-bit base64url token>"}` and the exact
  app `Origin`; returns `{authenticated:true}` with a signed cookie.
- `DELETE /api/session`: exact app `Origin`; expires the cookie and returns
  `{authenticated:false}`.
- `GET /api/book`: the indexed book manifest, after authorization.
- `GET|HEAD /api/assets/<exact inventory path>`: an indexed immutable JSON,
  MP3, or WebP object, after authorization.

Invite tokens belong in URL fragments, which the shell removes before exchanging
them. Query tokens are rejected. The cookie is `__Host-lumen_session`, `Secure`,
`HttpOnly`, `SameSite=Lax`, and scoped to `/`; it expires after 30 days or the
invite expiry, whichever comes first. Every request rechecks invite revocation,
expiry, and version. Updating the invite environment and redeploying revokes
previous sessions. Logout removes the current browser cookie.

Responses use `private, no-store`, `Vary: Cookie`, and same-origin resource
policy. Full book assets receive no cross-origin grant. An unauthenticated
request fails before reading the private index or invoking Blob. HEAD and
conditional requests authenticate too. The service worker must not intercept
these routes or audio Range requests.

## Server environment

Server-only values are supplied to the deployed Vercel Functions. Locally use
the machine's dotenvx wrapper with an ignored encrypted `.env.shared` and an
ignored owner-readable `.env.keys`; never put these values in frontend build
variables, JavaScript, static JSON, source-control commits, or command arguments.

Required names:

- `LUMEN_APP_ORIGIN`: `https://lumen-phi-five.vercel.app`, with no trailing slash.
- `LUMEN_SESSION_SECRET`: 32–64 random bytes encoded as canonical base64url.
- `LUMEN_INVITES_JSON`: at most 50 grants. Each is `{id,tokenHash,version,
  expiresAt,revoked}`. `tokenHash` is SHA-256 of the **base64url token string**;
  `expiresAt` is Unix seconds; `version` starts at 1; `revoked` is boolean.
  The original invite tokens remain in private operator records only.
- `LUMEN_RELEASE_INDEX_PATH`: immutable
  `lumen/releases/<releaseId>/server-index.<sha256>.json`.
- `LUMEN_RELEASE_INDEX_SHA256`: that file's exact byte hash.
- Blob credentials: prefer a project-connected `BLOB_STORE_ID` and automatic
  Vercel OIDC. The SDK also accepts server-only `BLOB_READ_WRITE_TOKEN` where
  OIDC is unavailable. Never read or pass the automatic OIDC token manually.

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

## Private upload and verification

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

## Audio seeking

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
