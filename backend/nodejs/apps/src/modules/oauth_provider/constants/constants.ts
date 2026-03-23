/**
 * Custom URI schemes whitelisted for OAuth redirect URIs.
 * These bypass the HTTPS requirement for redirect URI validation.
 */
export const ALLOWED_CUSTOM_REDIRECT_URIS = [
  'cursor://anysphere.cursor-mcp/oauth/callback',
]
