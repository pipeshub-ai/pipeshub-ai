'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ConnectorsApi } from '@/app/(main)/workspace/connectors/api';
import { isProcessedError } from '@/lib/api';
import {
  postConnectorOAuthErrorToOpener,
  postConnectorOAuthSuccessToOpener,
} from '@/app/(main)/connectors/oauth/connector-oauth-window-messages';
import { parseConnectorOAuthCallbackPayload } from '@/app/(main)/connectors/oauth/connector-oauth-callback-response';
import {
  OAUTH_CALLBACK_SUCCESS_DISPLAY_MS,
} from '@/app/(main)/workspace/connectors/components/authenticate-tab/use-connector-oauth-popup';

function userFacingCallbackError(e: unknown, fallback: string): string {
  if (isProcessedError(e)) return e.message;
  if (e instanceof Error && e.message.trim()) return e.message;
  return fallback;
}

function closePopup() {
  try {
    window.close();
  } catch {
    /* ignore */
  }
}

function connectorIdFromRedirect(redirectUrl: string | undefined): string | null {
  if (!redirectUrl) return null;
  try {
    const u = redirectUrl.startsWith('http')
      ? new URL(redirectUrl)
      : new URL(redirectUrl, window.location.origin);
    const segs = u.pathname.split('/').filter(Boolean);
    return segs[segs.length - 1] || null;
  } catch {
    const segs = redirectUrl.split('/').filter(Boolean);
    return segs[segs.length - 1] || null;
  }
}

type CallbackStatus = 'processing' | 'success' | 'error';

/**
 * Completes connector OAuth in a popup: exchanges `code` via the Node API (legacy parity),
 * notifies the opener with `CONNECTOR_OAUTH_*` postMessage types, then closes the window.
 */
export function ConnectorOAuthCallbackClient() {
  const [status, setStatus] = useState<CallbackStatus>('processing');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const ranRef = useRef(false);

  const closeWindow = useCallback(() => {
    closePopup();
  }, []);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    const run = async () => {
      try {
        const searchParams = new URLSearchParams(window.location.search);
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const oauthError = searchParams.get('error');
        const oauthErrorDescription = searchParams.get('error_description');

        if (oauthError) {
          const providerMessage =
            oauthErrorDescription?.trim() || oauthError || 'OAuth was cancelled or rejected.';
          throw new Error(providerMessage);
        }
        if (!code) {
          throw new Error('No authorization code received.');
        }
        if (!state) {
          throw new Error('No state parameter received.');
        }

        const data = await ConnectorsApi.completeConnectorOAuthCallback({
          code,
          state,
          oauthError: null,
          baseUrl: window.location.origin,
        });

        const { redirectUrl } = parseConnectorOAuthCallbackPayload(data);

        const connectorId = connectorIdFromRedirect(redirectUrl);
        // Notify opener immediately so the workspace panel can refetch while persistence catches up
        // (waiting until close delayed refresh by ~900ms and missed the best polling window).
        postConnectorOAuthSuccessToOpener(connectorId);

        setStatus('success');
        setMessage('Authentication complete. You can close this window.');
        setTimeout(() => {
          closeWindow();
        }, OAUTH_CALLBACK_SUCCESS_DISPLAY_MS);
      } catch (e) {
        const detail = userFacingCallbackError(e, 'OAuth authentication failed.');
        setStatus('error');
        setError(detail);
        postConnectorOAuthErrorToOpener(detail);
      }
    };

    void run();
  }, [closeWindow]);

  const shellStyle = {
    minHeight: '100vh',
    width: '100%',
    padding: 'var(--space-5)',
    background: 'linear-gradient(180deg, var(--olive-2) 0%, var(--olive-1) 100%)',
  } as const;

  const columnStyle = {
    width: '100%',
    maxWidth: 432,
    textAlign: 'center' as const,
  };

  let statusPanel: ReactNode;
  switch (status) {
    case 'processing':
      statusPanel = (
        <Flex direction="column" align="center" gap="5">
          <Box style={{ width: 48, height: 48, flexShrink: 0 }}>
            <LottieLoader variant="still" size={48} />
          </Box>
          <Flex direction="column" align="center" gap="1">
            <Text
              as="p"
              size="4"
              weight="medium"
              style={{ margin: 0, color: 'var(--gray-12)', letterSpacing: '-0.04px', lineHeight: '26px' }}
            >
              Completing sign-in…
            </Text>
            <Text
              as="p"
              size="3"
              style={{
                margin: 0,
                color: 'var(--gray-10)',
                lineHeight: '24px',
                maxWidth: 432,
              }}
            >
              Securely exchanging the authorization code with PipesHub.
            </Text>
          </Flex>
        </Flex>
      );
      break;
    case 'success':
      statusPanel = (
        <Flex direction="column" align="center" gap="5">
          <Box
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '5.333px',
              borderRadius: '5.333px',
              backgroundColor: 'var(--accent-a2)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="check" size={21} color="var(--accent-11)" />
          </Box>
          <Text
            as="p"
            size="4"
            weight="medium"
            style={{
              margin: 0,
              color: 'var(--accent-11)',
              letterSpacing: '-0.04px',
              lineHeight: '26px',
            }}
          >
            Connected
          </Text>
          <Text as="p" size="3" style={{ margin: 0, color: 'var(--gray-10)', lineHeight: '24px' }}>
            {message}
          </Text>
        </Flex>
      );
      break;
    case 'error':
      statusPanel = (
        <Flex direction="column" align="center" gap="5">
          <Box
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '5.333px',
              borderRadius: '5.333px',
              backgroundColor: 'var(--red-a2)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="warning" size={21} color="var(--red-11)" />
          </Box>
          <Text
            as="p"
            size="4"
            weight="medium"
            style={{
              margin: 0,
              color: 'var(--red-10)',
              letterSpacing: '-0.04px',
              lineHeight: '26px',
            }}
          >
            Authentication failed
          </Text>
          <Text
            as="p"
            size="3"
            style={{
              margin: 0,
              color: 'var(--gray-10)',
              lineHeight: '24px',
              wordBreak: 'break-word',
            }}
          >
            {error}
          </Text>
          <Button size="2" variant="outline" color="gray" onClick={closeWindow}>
            Close
          </Button>
        </Flex>
      );
      break;
    default:
      statusPanel = null;
  }

  return (
    <Flex align="center" justify="center" style={shellStyle}>
      <Box style={columnStyle}>{statusPanel}</Box>
    </Flex>
  );
}
