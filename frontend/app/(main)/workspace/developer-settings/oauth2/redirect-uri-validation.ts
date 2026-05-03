/**
 * Redirect URI rules mirror the API service (not only Zod on the route):
 * `OAuthAppService.validateRedirectUris` in
 * `backend/nodejs/apps/src/modules/oauth_provider/services/oauth.app.service.ts`
 *
 * Request bodies are first checked with `z.string().url()` in
 * `oauth.validators.ts`, then create/update runs the stricter redirect rules above.
 *
 * Keep `ALLOWED_CUSTOM_REDIRECT_URIS` in sync with:
 * `backend/nodejs/apps/src/modules/oauth_provider/constants/constants.ts`
 */
export const ALLOWED_CUSTOM_REDIRECT_URIS: readonly string[] = [
  'cursor://anysphere.cursor-mcp/oauth/callback',
];

const BLOCKED_REDIRECT_PROTOCOLS = new Set([
  'javascript:',
  'data:',
  'vbscript:',
]);

/** Optional homepage / privacy / terms: only http(s), matches prior UI and typical public URLs. */
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

/**
 * Same acceptance as `OAuthAppService.validateRedirectUris` (whitelist, then
 * https or localhost/127.0.0.1, no fragment).
 */
export function isValidOAuthRedirectUri(s: string): boolean {
  const t = s.trim();
  if (!t) return false;
  if (ALLOWED_CUSTOM_REDIRECT_URIS.includes(t)) {
    return true;
  }
  try {
    const parsed = new URL(t);
    const p = parsed.protocol.toLowerCase();
    if (BLOCKED_REDIRECT_PROTOCOLS.has(p)) return false;
    if (
      parsed.protocol !== 'https:' &&
      parsed.hostname !== 'localhost' &&
      parsed.hostname !== '127.0.0.1'
    ) {
      return false;
    }
    if (parsed.hash) {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}
