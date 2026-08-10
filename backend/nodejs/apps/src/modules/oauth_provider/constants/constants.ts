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

/**
 * clientId prefix on the per-org synthetic "Personal Access Tokens" app
 * that PATs are minted against (see {@link PatService}). Not a real,
 * user-manageable OAuth app — {@link OAuthAppService} excludes it from
 * list/get/update/suspend/delete/regenerate-secret so its owner can't
 * suspend, edit grant types on, or pull a working secret for it through
 * the regular OAuth-clients UI.
 */
export const PAT_APP_CLIENT_ID_PREFIX = 'pat-system:'
