/**
 * API origin for OAuth, Socket.IO RPC, and REST — from `PIPESHUB_BACKEND_URL` only
 * (not persisted). When unset, defaults to local dev.
 */
export function getBackendBaseUrl(): string {
  const t = process.env.PIPESHUB_BACKEND_URL?.trim();
  return (t || "http://localhost:3000").replace(/\/$/, "");
}

/** True when the user explicitly set `PIPESHUB_BACKEND_URL` (non-empty). */
export function hasBackendUrlFromEnv(): boolean {
  return Boolean(process.env.PIPESHUB_BACKEND_URL?.trim());
}
