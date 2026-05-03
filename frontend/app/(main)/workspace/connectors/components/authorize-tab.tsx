'use client';

import React from 'react';
import { Flex, Text, Separator, Callout } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { AuthCard } from './auth-card';
import { useConnectorsStore } from '../store';
import { isConnectorInstanceAuthenticatedForUi } from '../utils/auth-helpers';
import type { AuthCardState } from '../types';

/** Same shell as `SyncSettingsSection` / Authenticate olive cards. */
const sectionCardStyle = {
  padding: 16,
  backgroundColor: 'var(--olive-2)',
  borderRadius: 'var(--radius-2)',
  border: '1px solid var(--olive-3)',
  width: '100%' as const,
  boxSizing: 'border-box' as const,
};

export type AuthorizeTabProps = {
  startOAuthPopup: () => void | Promise<void>;
  isAuthenticating: boolean;
};

/**
 * Browser OAuth step after the instance exists (connector id required).
 * Credentials and OAuth app live on the Authenticate tab; this tab only runs consent.
 *
 * `startOAuthPopup` / `isAuthenticating` are owned by {@link ConnectorPanel} so the OAuth
 * `postMessage` listener stays mounted while the panel is open (Radix Tabs may unmount this tab).
 */
export function AuthorizeTab({ startOAuthPopup, isAuthenticating }: AuthorizeTabProps) {
  const panelConnector = useConnectorsStore((s) => s.panelConnector);
  const panelConnectorId = useConnectorsStore((s) => s.panelConnectorId);
  const connectorConfig = useConnectorsStore((s) => s.connectorConfig);
  const authState = useConnectorsStore((s) => s.authState);

  if (!panelConnector || !panelConnectorId) return null;

  const cardState: AuthCardState =
    authState === 'authenticating' ? 'empty' : (authState as AuthCardState);

  const authed = isConnectorInstanceAuthenticatedForUi(
    panelConnectorId,
    panelConnector,
    connectorConfig
  );
  const reauthFailed = authed && authState === 'failed';
  /**
   * True while the popup + verification flow is active. Using only `isAuthenticating` (not
   * `authState === 'authenticating'`) mirrors the toolset pattern: the hook explicitly resets
   * `authState` to `'empty'` on failure, so relying on `authState` here would leave the button
   * stuck in loading if the hook reset `isAuthenticating` before `authState` caught up.
   */
  const consentOauthBusy = isAuthenticating;
  const reauthOauthBusy = isAuthenticating;

  return (
    <Flex direction="column" gap="6" style={{ padding: '4px 0', width: '100%', minWidth: 0 }}>
      {!authed ? (
        <Flex direction="column" gap="4" style={sectionCardStyle}>
          <Flex direction="column" gap="1">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Sign in with your provider
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              Open your identity provider&apos;s sign-in window and approve access. When sign-in
              succeeds, choose <Text weight="medium">Continue to configuration</Text> in the footer
              to set up sync, filters, and records.
            </Text>
          </Flex>

          <AuthCard
            embedded
            state={cardState}
            connectorName={panelConnector.name}
            onAuthenticate={() => void startOAuthPopup()}
            onRetry={() => void startOAuthPopup()}
            loading={consentOauthBusy}
          />
        </Flex>
      ) : (
        <Flex direction="column" gap="4" style={sectionCardStyle}>
          <Flex direction="column" gap="1">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Authorization status
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              This instance can access your data at the provider. Continue to configure sync and
              indexing, or sign in again if you revoked access or rotated credentials.
            </Text>
          </Flex>

          <Callout.Root color="green" variant="surface" size="1">
            <Callout.Icon>
              <MaterialIcon name="check_circle" size={16} color="var(--green-11)" />
            </Callout.Icon>
            <Callout.Text size="2" weight="medium" style={{ color: 'var(--green-12)' }}>
              Connected — you can continue to Configure records
            </Callout.Text>
          </Callout.Root>

          {reauthFailed ? (
            <Callout.Root color="red" variant="surface" size="1">
              <Callout.Icon>
                <MaterialIcon name="error_outline" size={16} color="var(--red-11)" />
              </Callout.Icon>
              <Callout.Text size="2" style={{ color: 'var(--red-11)', lineHeight: 1.5 }}>
                Sign-in did not complete. Try again, or check your identity provider settings.
              </Callout.Text>
            </Callout.Root>
          ) : null}

          <Separator size="4" style={{ width: '100%', maxWidth: '100%' }} />

          <Flex direction="column" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              Refresh access
            </Text>
            <Text size="1" style={{ color: 'var(--gray-10)', lineHeight: 1.55 }}>
              Same as the legacy Reauthenticate action when tokens need to be renewed.
            </Text>
            <LoadingButton
              type="button"
              variant="outline"
              color="gray"
              size="2"
              loading={reauthOauthBusy}
              loadingLabel="Authenticating…"
              style={{
                width: '100%',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 8,
              }}
              onClick={() => void startOAuthPopup()}
            >
              <MaterialIcon name="vpn_key" size={16} color="var(--gray-11)" />
              Re-authenticate with provider
            </LoadingButton>
          </Flex>
        </Flex>
      )}
    </Flex>
  );
}
