// Schemes that run script or read local content. Native apps' own schemes (cursor://, com.example.app:/) stay allowed.
const BLOCKED_SCHEMES = new Set(['javascript:', 'data:', 'vbscript:', 'file:', 'about:', 'blob:', 'filesystem:']);

/** True when a server-supplied redirect may be navigated to. `new URL` drops tabs and case, so neither hides the scheme. */
export function isSafeRedirectUrl(url: string): boolean {
  try {
    return !BLOCKED_SCHEMES.has(new URL(url).protocol);
  } catch {
    return false;
  }
}
