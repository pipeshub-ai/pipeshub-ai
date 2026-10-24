'use client';

import { useTranslation } from 'react-i18next';
import { Text } from '@radix-ui/themes';

/** True when `secret` is filled in but doesn't start the way the provider's secrets do. */
export function secretMissesPrefix(secret: string, prefix: string | null | undefined): boolean {
  const value = secret.trim();
  return Boolean(prefix && value && !value.startsWith(prefix));
}

/**
 * Under the client secret field, for providers whose secrets start with a fixed prefix (Google's
 * `GOCSPX-`): says so, and warns when the entered secret lacks it. Copying a secret by double-click
 * stops at the hyphen, and the provider then refuses every sign-in as `invalid_client`.
 */
export function McpClientSecretPrefixHint({ prefix, secret }: { prefix?: string | null; secret: string }) {
  const { t } = useTranslation();
  if (!prefix) return null;
  const missing = secretMissesPrefix(secret, prefix);
  return (
    <Text
      size="1"
      role={missing ? 'alert' : undefined}
      data-testid="mcp-client-secret-prefix-hint"
      style={{ color: missing ? 'var(--amber-11)' : 'var(--slate-10)' }}
    >
      {t(
        missing
          ? 'workspace.mcpServers.oauthConfig.clientSecretPrefixMissing'
          : 'workspace.mcpServers.oauthConfig.clientSecretPrefixHint',
        { prefix }
      )}
    </Text>
  );
}
