// ========================================
// Auth type helper utilities
// ========================================

/** Check if auth type requires no authentication (skip auth card) */
export function isNoneAuthType(authType: string): boolean {
  return authType === 'NONE' || authType === '';
}

/** Check if auth type uses OAuth redirect flow (show authenticate button) */
export function isOAuthType(authType: string): boolean {
  return ['OAUTH', 'OAUTH_ADMIN_CONSENT', 'OAUTH_CERTIFICATE'].includes(authType);
}

/** Check if auth type uses credential fields (show form fields) */
export function isCredentialAuthType(authType: string): boolean {
  return ['API_TOKEN', 'USERNAME_PASSWORD', 'BEARER_TOKEN', 'CUSTOM'].includes(authType);
}
