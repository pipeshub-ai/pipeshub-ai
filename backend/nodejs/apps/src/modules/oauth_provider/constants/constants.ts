/**
 * Exact-match custom URI schemes whitelisted for OAuth redirect URIs.
 * These bypass the HTTPS requirement for redirect URI validation.
 */
export const ALLOWED_CUSTOM_REDIRECT_URIS = [
  'cursor://anysphere.cursor-mcp/oauth/callback',
]

/**
 * Custom URI scheme prefixes accepted for native MCP clients.
 * These are allowed at any path/host within the scheme, e.g. `claude-ai://oauth/...`.
 *
 * RFC 8252 §7.1 — native apps register a private-use URI scheme they own.
 */
export const ALLOWED_NATIVE_URI_SCHEMES = [
  'cursor:',
  'claude-ai:',
  'claude-desktop:',
  'mcp:',
  'mcp-inspector:',
]
