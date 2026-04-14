/**
 * Validation for OAuth redirect URIs in the developer settings UI.
 * Supports https (and http where applicable) plus custom URI schemes used by
 * native/desktop OAuth clients (e.g. cursor://… for Cursor MCP OAuth callback).
 */

const BLOCKED_CUSTOM_REDIRECT_PROTOCOLS = new Set([
  'javascript:',
  'data:',
  'vbscript:',
]);

export function isValidHttpUrl(s: string): boolean {
  const t = s.trim();
  if (!t) return false;
  try {
    const u = new URL(t);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

export function isValidOAuthRedirectUri(s: string): boolean {
  if (isValidHttpUrl(s)) return true;
  const t = s.trim();
  if (!t) return false;
  try {
    const u = new URL(t);
    const p = u.protocol.toLowerCase();
    if (BLOCKED_CUSTOM_REDIRECT_PROTOCOLS.has(p)) return false;
    return /^[a-z][a-z0-9+.-]*:$/i.test(p);
  } catch {
    return false;
  }
}
