/**
 * Custom URI schemes whitelisted for OAuth redirect URIs.
 * These bypass the HTTPS requirement for redirect URI validation.
 */
export const ALLOWED_CUSTOM_REDIRECT_URIS = [
  'cursor://anysphere.cursor-mcp/oauth/callback',
]

/**
 * Literal prefix on personal access tokens, ahead of the underlying JWT.
 * Unlike a bare JWT, a fixed prefix is trivially grep-able, so operators
 * can build leak detection / secret scanning around it. Stripped by
 * {@link OAuthTokenService.verifyAccessToken} before hashing/verifying,
 * so it never affects the token's cryptographic contents.
 */
export const PAT_TOKEN_PREFIX = 'phpat_'
