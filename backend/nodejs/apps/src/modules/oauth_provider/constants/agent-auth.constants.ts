/**
 * The auth.md agent-registration protocol (service_auth). Its URNs and the
 * assertion type are WorkOS-namespaced and may be renamed; keep them here.
 */
export const AGENT_CLAIM_GRANT = 'urn:workos:agent-auth:grant-type:claim';
export const JWT_BEARER_GRANT = 'urn:ietf:params:oauth:grant-type:jwt-bearer';
export const ASSERTION_TYP = 'oauth-id-jag+jwt';

export const REGISTRATION_ID_PREFIX = 'reg_';
export const CLAIM_TOKEN_PREFIX = 'clm_';

export const CLAIM_CODE_TTL_SECONDS = 600;
export const CLAIM_POLL_INTERVAL_SECONDS = 5;
export const REGISTRATION_WINDOW_SECONDS = 24 * 60 * 60;
export const ASSERTION_TTL_SECONDS = 30 * 24 * 60 * 60;
/** A row is kept past the longest assertion it can back (issued up to a window after creation). */
export const REGISTRATION_RETENTION_SECONDS =
  REGISTRATION_WINDOW_SECONDS + ASSERTION_TTL_SECONDS + 24 * 60 * 60;
export const MAX_FAILED_CODE_ATTEMPTS = 5;

/** Registration works exactly when first-party device login does. */
export const agentAuthEnabled = (): boolean =>
  process.env.PIPESHUB_ENABLE_DEVICE_GRANT !== 'false';
