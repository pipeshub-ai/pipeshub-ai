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

/**
 * clientId of the instance-wide first-party device app ("PipesHub agent").
 * Public client, device_code + refresh_token only. Advertised as
 * `pipeshub_device_client_id` on OIDC discovery so agents can start the
 * TV-code flow without DCR or an admin-created OAuth app. Hidden from
 * Developer Settings the same way as {@link PAT_APP_CLIENT_ID_PREFIX}.
 */
export const FIRST_PARTY_DEVICE_CLIENT_ID = 'pipeshub-agent'

/**
 * Literal prefix on service tokens, the credential a service account
 * authenticates with. Deliberately different from {@link PAT_TOKEN_PREFIX}:
 * a leaked `phsvc_` token is a machine identity that may be read by a whole
 * team's automation, while a leaked `phpat_` token belongs to one person.
 * They warrant different responses, so a scanner and anyone reading a log
 * should be able to tell them apart at a glance.
 */
export const SERVICE_TOKEN_PREFIX = 'phsvc_'

/**
 * clientId prefix on the per-org synthetic "Service Tokens" app that service
 * tokens are minted against, mirroring {@link PAT_APP_CLIENT_ID_PREFIX}.
 * Hidden from Developer Settings the same way.
 */
export const SERVICE_TOKEN_APP_CLIENT_ID_PREFIX = 'svc-system:'

/**
 * Every display-only prefix a credential may carry ahead of its JWT.
 *
 * These exist for humans and secret scanners, never for the cryptography, so
 * they are stripped before a token is verified or hashed. Keeping the list
 * and the stripping in one place is what stops a new prefix from being
 * honoured by one verifier and rejected by another.
 */
export const TOKEN_DISPLAY_PREFIXES: readonly string[] = [
  PAT_TOKEN_PREFIX,
  SERVICE_TOKEN_PREFIX,
]

/** Returns the bare JWT, with any display prefix removed. */
export function stripTokenDisplayPrefix(token: string): string {
  const prefix = TOKEN_DISPLAY_PREFIXES.find((candidate) =>
    token.startsWith(candidate),
  )
  return prefix === undefined ? token : token.slice(prefix.length)
}
