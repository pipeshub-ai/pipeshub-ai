'use client';

import React, { useCallback } from 'react';
import { AuthCard } from '../auth-card';
import { useConnectorsStore } from '../../store';
import type { AuthCardState } from '../../types';

// ========================================
// OAuthSection
// ========================================

export function OAuthSection({
  cardState,
  connectorName,
  isLoading,
}: {
  cardState: AuthCardState;
  connectorName: string;
  isLoading: boolean;
}) {
  const { setAuthState, panelConnectorId } = useConnectorsStore();

  const handleAuthenticate = useCallback(async () => {
    if (!panelConnectorId) return;

    setAuthState('authenticating');

    try {
      // Dynamic import to avoid circular deps
      const { ConnectorsApi } = await import('../../api');
      const { authorizationUrl } = await ConnectorsApi.getOAuthAuthorizationUrl(
        panelConnectorId
      );

      // Open OAuth in a popup
      const popup = window.open(
        authorizationUrl,
        'oauth-popup',
        'width=600,height=700,scrollbars=yes'
      );

      // Poll for popup close
      if (popup) {
        const pollTimer = setInterval(() => {
          if (popup.closed) {
            clearInterval(pollTimer);
            // After popup closes, check config to see if auth succeeded
            checkAuthStatus(panelConnectorId);
          }
        }, 500);
      }
    } catch {
      setAuthState('failed');
    }
  }, [panelConnectorId, setAuthState]);

  const handleRetry = useCallback(() => {
    handleAuthenticate();
  }, [handleAuthenticate]);

  return (
    <AuthCard
      state={cardState}
      connectorName={connectorName}
      onAuthenticate={handleAuthenticate}
      onRetry={handleRetry}
      loading={isLoading}
    />
  );
}

// ========================================
// Helpers
// ========================================

/**
 * Check if authentication was successful after OAuth redirect.
 */
async function checkAuthStatus(connectorId: string) {
  try {
    const { ConnectorsApi } = await import('../../api');
    const config = await ConnectorsApi.getConnectorConfig(connectorId);
    const store = useConnectorsStore.getState();
    if (config.isAuthenticated) {
      store.setAuthState('success');
    } else {
      store.setAuthState('failed');
    }
  } catch {
    useConnectorsStore.getState().setAuthState('failed');
  }
}
