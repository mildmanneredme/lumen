import { bindings, defineConfig } from "cf/config";
import uploadPolicy from "../scripts/r2-worker-adapter.cjs";

export default defineConfig(({ mode }) => {
  const policy = mode === "upload" ? uploadPolicy.createReleaseStoragePolicy({
    releaseId: process.env.LUMEN_UPLOAD_RELEASE_ID ?? uploadPolicy.DEFAULT_RELEASE_ID,
    readOnlyReleaseIds: JSON.parse(process.env.LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON ?? "[]"),
  }) : null;
  return {
  accountId: process.env.CLOUDFLARE_ACCOUNT_ID,
  worker: {
    name: mode === "upload" ? "lumen-upload" : "lumen-media",
    entrypoint: mode === "upload" ? "./upload-worker.mjs" : "./worker.mjs",
    compatibilityDate: "2026-10-10",
    compatibilityFlags: ["nodejs_compat"],
    workersDev: true,
    previewUrls: false,
    logpush: false,
    observability: { enabled: false, redactQueryString: true },
    env: mode === "upload" ? {
      LUMEN_R2: bindings.r2({ name: "lumen-private" }),
      LUMEN_UPLOAD_SECRET: bindings.secret(),
      LUMEN_UPLOAD_PREFIX: bindings.text(policy.prefix),
      LUMEN_UPLOAD_READ_ONLY_RELEASES_JSON: bindings.text(JSON.stringify(policy.readOnlyReleaseIds)),
    } : {
      LUMEN_R2: bindings.r2({ name: "lumen-private" }),
      LUMEN_APP_ORIGIN: bindings.secret(),
      LUMEN_WORKER_ORIGIN: bindings.secret(),
      LUMEN_ALLOWED_ORIGINS_JSON: bindings.secret(),
      LUMEN_SESSION_SECRET: bindings.secret(),
      LUMEN_INVITES_JSON: bindings.secret(),
      LUMEN_BRIDGE_KEY: bindings.secret(),
      LUMEN_MEDIA_SECRET: bindings.secret(),
      LUMEN_RELEASE_INDEX_PATH: bindings.secret(),
      LUMEN_RELEASE_INDEX_SHA256: bindings.secret(),
    },
  },
  };
});
