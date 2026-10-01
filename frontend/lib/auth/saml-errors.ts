/** Backend SAML error codes → short user-facing descriptions. */
const SAML_ERROR_DESCRIPTIONS: Record<string, string> = {
  jit_Disabled: 'JIT provisioning is disabled for your organisation',
  jit_disabled: 'JIT provisioning is disabled for your organisation',
  Saml_sso_disabled: 'SAML SSO is not enabled for your organisation',
  saml_sso_disabled: 'SAML SSO is not enabled for your organisation',
  auth_failed: 'SAML authentication failed. Please try again',
  unknown: 'An unexpected error occurred during sign-in. Please try again',
};

export function getSamlErrorDescription(code: string): string {
  return SAML_ERROR_DESCRIPTIONS[code] ?? code.replace(/_/g, ' ');
}
