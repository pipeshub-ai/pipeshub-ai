import type { ConnectorAuthConfig, AuthSchemaField } from '../../types';

/**
 * Resolve auth fields from schema based on selected auth type.
 * Handles both single-schema and multi-schema formats.
 */
export function resolveAuthFields(
  authConfig: ConnectorAuthConfig | undefined | null,
  selectedAuthType: string
): AuthSchemaField[] {
  if (!authConfig) return [];

  // Multi-schema format (keyed by auth type)
  if (authConfig.schemas && selectedAuthType && authConfig.schemas[selectedAuthType]) {
    return authConfig.schemas[selectedAuthType].fields ?? [];
  }

  // Single schema format
  if (authConfig.schema?.fields) {
    return authConfig.schema.fields;
  }

  return [];
}

/**
 * Format auth type enum to display name.
 */
export function formatAuthTypeName(authType: string): string {
  const map: Record<string, string> = {
    OAUTH: 'OAuth 2.0',
    OAUTH_ADMIN_CONSENT: 'OAuth (Admin Consent)',
    OAUTH_CERTIFICATE: 'OAuth (Certificate)',
    API_TOKEN: 'API Token',
    USERNAME_PASSWORD: 'Username & Password',
    BEARER_TOKEN: 'Bearer Token',
    CUSTOM: 'Custom',
    NONE: 'None',
  };
  return map[authType] || authType;
}
