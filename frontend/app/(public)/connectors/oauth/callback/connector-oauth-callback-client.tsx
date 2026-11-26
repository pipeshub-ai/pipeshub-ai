'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import NextLink from 'next/link';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ConnectorsApi } from '@/app/(main)/workspace/connectors/api';
import { isProcessedError } from '@/lib/api';
import { failOnSessionEnd } from '@/lib/api/session-cache';
import { integrationHandoffLink } from '@/lib/auth/desktop-oauth';
import DesktopHandoffNotice from '@/app/(public)/auth/desktop-handoff-notice';
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

/** A sign-in started from an emailed connect link: its base64url JSON state names the user (`linkUser`). */
function isEmailLinkState(state: string): boolean {
  try {
    const parsed: unknown = JSON.parse(atob(state.replace(/-/g, '+').replace(/_/g, '/')));
    return typeof parsed === 'object' && parsed !== null && Boolean((parsed as { linkUser?: unknown }).linkUser);
  } catch {
    return false;
  }
}

/** `error` null: connected. */
type LinkResult = { error: string | null; name?: string };

/**
 * Completes connector OAuth in a popup: exchanges `code` via the Node API (legacy parity),
 * notifies the opener with `CONNECTOR_OAUTH_*` postMessage types, then closes the window.
 */
export function ConnectorOAuthCallbackClient() {
  const { t } = useTranslation();
  // Set for a sign-in from an emailed link: no opener and maybe no session, so the result shows here.
  const [linkResult, setLinkResult] = useState<LinkResult | null>(null);
  const [status, setStatus] = useState<CallbackStatus>('processing');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const ranRef = useRef(false);
  const [handoffLink, setHandoffLink] = useState<string | null>(null);

  const closeWindow = useCallback(() => {
    closePopup();
  }, []);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    // Started from the desktop app: hand code+state to it rather than completing here with
    // this browser's session, which may be signed out or belong to someone else.
    const handoff = integrationHandoffLink('connector', new URLSearchParams(window.location.search));
    if (handoff) {
      setHandoffLink(handoff);
      window.location.href = handoff;
      return;
    }

    const run = async () => {
      try {
        const searchParams = new URLSearchParams(window.location.search);
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const oauthError = searchParams.get('error');
        const oauthErrorDescription = searchParams.get('error_description');
        const signInFailed = t('workspace.connectors.userAccess.link.signInFailed');
        // The provider's error code is not a sentence; `access_denied` means the user pressed Cancel.
        const providerMessage = oauthError
          ? oauthError === 'access_denied'
            ? t('workspace.connectors.userAccess.link.cancelled')
            : oauthErrorDescription?.trim() || signInFailed
          : null;

        if (state && isEmailLinkState(state)) {
          if (providerMessage || !code) {
            setLinkResult({ error: providerMessage || signInFailed });
            return;
          }
          try {
            const result = await ConnectorsApi.completeUserAccessLink(code, state);
            setLinkResult(
              result.success
                ? { error: null, name: result.connectorName }
                : { error: result.error_message || t('workspace.connectors.userAccess.link.error') }
            );
          } catch {
            setLinkResult({ error: t('workspace.connectors.userAccess.link.error') });
          }
          return;
        }

        if (providerMessage) {
          throw new Error(providerMessage);
        }
        if (!code || !state) {
          throw new Error(signInFailed);
        }

        // A sign-out (e.g. in another tab) drops the answer: fail into the error state rather than spin forever.
        const data = await failOnSessionEnd(
          ConnectorsApi.completeConnectorOAuthCallback({
            code,
            state,
            oauthError: null,
            baseUrl: window.location.origin,
          }),
        );

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
  }, [closeWindow, t]);

  if (handoffLink) {
    return <DesktopHandoffNotice deepLink={handoffLink} titleKey="auth.desktopHandoff.integrationTitle" />;
  }

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
  if (linkResult) {
    const ok = !linkResult.error;
    statusPanel = (
      <Flex direction="column" align="center" gap="4">
        <MaterialIcon
          name={ok ? 'check' : 'error_outline'}
          size={21}
          color={ok ? 'var(--accent-11)' : 'var(--red-11)'}
        />
        {ok ? (
          <Text as="p" size="3" style={{ margin: 0, color: 'var(--gray-12)', lineHeight: '24px' }}>
            {linkResult.name
              ? t('workspace.connectors.userAccess.link.done', { name: linkResult.name })
              : t('workspace.connectors.userAccess.link.doneUnnamed')}
          </Text>
        ) : (
          <>
            <Text as="p" size="4" weight="medium" style={{ margin: 0, color: 'var(--red-11)' }}>
              {t('workspace.connectors.userAccess.link.failedTitle')}
            </Text>
            <Text as="p" size="3" style={{ margin: 0, color: 'var(--gray-11)', wordBreak: 'break-word' }}>
              {linkResult.error}
            </Text>
            <Text as="p" size="3" style={{ margin: 0, color: 'var(--gray-11)' }}>
              {t('workspace.connectors.userAccess.link.failedRetry')}
            </Text>
          </>
        )}
        <Button asChild size="2" variant={ok ? 'solid' : 'outline'} color={ok ? undefined : 'gray'} style={{ minHeight: 44 }}>
          <NextLink href="/">{t('workspace.connectors.userAccess.link.openApp')}</NextLink>
        </Button>
      </Flex>
    );
  } else switch (status) {
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
                color: 'var(--gray-11)',
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
          <Text as="p" size="3" style={{ margin: 0, color: 'var(--gray-11)', lineHeight: '24px' }}>
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
            <MaterialIcon name="error_outline" size={21} color="var(--red-11)" />
          </Box>
          <Flex direction="column" align="center" gap="1">
            <Text
              as="p"
              size="4"
              weight="medium"
              style={{
                margin: 0,
                color: 'var(--red-11)',
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
                color: 'var(--gray-11)',
                lineHeight: '24px',
                wordBreak: 'break-word',
              }}
            >
              {error}
            </Text>
          </Flex>
          <Button size="2" variant="outline" color="gray" onClick={closeWindow}>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 'var(--space-2)',
              }}
            >
              <MaterialIcon name="close" size={16} color="var(--gray-11)" />
              Close
            </span>
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
