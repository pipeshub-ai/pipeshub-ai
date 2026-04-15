'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ToolsetsApi } from '@/app/(main)/agents/toolsets-api';
import { isProcessedError } from '@/lib/api';
import {
  postToolsetOAuthErrorToOpener,
  postToolsetOAuthSuccessToOpener,
} from '@/app/(main)/toolsets/oauth/toolset-oauth-window-messages';
import { parseToolsetOAuthCallbackSlug } from '@/app/(main)/toolsets/oauth/parse-toolset-oauth-callback-slug';

/** `apiClient` errors are normalized by the axios interceptor to `ProcessedError`. */
function userFacingCallbackError(e: unknown, fallback: string): string {
  if (isProcessedError(e)) return e.message;
  if (e instanceof Error && e.message.trim()) return e.message;
  return fallback;
}

type CallbackStatus = 'processing' | 'success' | 'error';

function closePopup() {
  try {
    window.close();
  } catch {
    /* ignore */
  }
}

/**
 * Completes toolset OAuth in a popup: exchanges `code` via the Node API (same contract as the legacy SPA),
 * notifies the opener with `oauth-success` / `oauth-error`, then closes the window.
 *
 * The toolset slug (for optional `postMessage` metadata) is taken from the URL path when present
 * (`/toolsets/oauth/callback/:slug`); no build-time slug list is required.
 */
export function ToolsetOAuthCallbackClient() {
  const { t } = useTranslation();
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
      const toolsetSlug = parseToolsetOAuthCallbackSlug(window.location.pathname);

      try {
        const searchParams = new URLSearchParams(window.location.search);
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const oauthError = searchParams.get('error');
        const oauthErrorDescription = searchParams.get('error_description');

        if (oauthError) {
          const providerMessage =
            oauthErrorDescription?.trim() ||
            oauthError ||
            t('agentBuilder.toolsetOAuthErrorRetryHint');
          throw new Error(t('agentBuilder.toolsetOAuthProviderError', { error: providerMessage }));
        }
        if (!code) {
          throw new Error(t('agentBuilder.toolsetOAuthNoCode'));
        }
        if (!state) {
          throw new Error(t('agentBuilder.toolsetOAuthNoState'));
        }

        const data = await ToolsetsApi.completeToolsetOAuthCallback({
          code,
          state,
          oauthError,
          baseUrl: window.location.origin,
        });

        const ok =
          Boolean(data?.success) ||
          Boolean(data?.redirectUrl) ||
          Boolean(data?.redirect_url);

        if (!ok) {
          throw new Error(t('agentBuilder.toolsetOAuthBackendRejected'));
        }

        setStatus('success');
        setMessage(t('agentBuilder.toolsetOAuthSuccessBody'));
        setTimeout(() => {
          postToolsetOAuthSuccessToOpener(toolsetSlug);
          closeWindow();
        }, 1200);
      } catch (e) {
        const detail = userFacingCallbackError(e, t('agentBuilder.toolsetOAuthErrorRetryHint'));
        setStatus('error');
        setError(detail);
        postToolsetOAuthErrorToOpener(toolsetSlug, detail);
      }
    };

    void run();
  }, [closeWindow, t]);

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

  const errorRetryHint = t('agentBuilder.toolsetOAuthErrorRetryHint');
  const showErrorDetail = error.trim() !== errorRetryHint.trim();

  let statusPanel: ReactNode;
  switch (status) {
    case 'processing':
      statusPanel = (
        <Flex direction="column" align="center" gap="5">
          <Box key="toolset-oauth-processing-loader" style={{ width: 48, height: 48, flexShrink: 0 }}>
            <LottieLoader variant="still" size={48} />
          </Box>
          <Flex key="toolset-oauth-processing-copy" direction="column" align="center" gap="1">
            <Text
              key="toolset-oauth-processing-title"
              as="p"
              size="4"
              weight="medium"
              style={{ margin: 0, color: 'var(--gray-12)', letterSpacing: '-0.04px', lineHeight: '26px' }}
            >
              {t('agentBuilder.toolsetOAuthCompletingTitle')}
            </Text>
            <Text
              key="toolset-oauth-processing-hint"
              as="p"
              size="3"
              style={{
                margin: 0,
                color: 'var(--gray-10)',
                lineHeight: '24px',
                maxWidth: 432,
              }}
            >
              {t('agentBuilder.toolsetOAuthProcessingHint')}
            </Text>
          </Flex>
        </Flex>
      );
      break;
    case 'success':
      statusPanel = (
        <Flex direction="column" align="center" gap="5">
          <Box
            key="toolset-oauth-success-badge"
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
          <Flex key="toolset-oauth-success-copy" direction="column" align="center" gap="1">
            <Text
              key="toolset-oauth-success-title"
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
              {t('agentBuilder.toolsetOAuthSuccessTitle')}
            </Text>
            <Text
              key="toolset-oauth-success-body"
              as="p"
              size="3"
              style={{ margin: 0, color: 'var(--gray-10)', lineHeight: '24px' }}
            >
              {message}
            </Text>
          </Flex>
        </Flex>
      );
      break;
    case 'error':
      statusPanel = (
        <Flex direction="column" align="center" gap="5">
          <Box
            key="toolset-oauth-error-badge"
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
          <Flex key="toolset-oauth-error-copy" direction="column" align="center" gap="1" style={{ width: '100%' }}>
            <Text
              key="toolset-oauth-error-title"
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
              {t('agentBuilder.toolsetOAuthFailedTitle')}
            </Text>
            <Flex
              key="toolset-oauth-error-messages"
              direction="column"
              align="center"
              gap="1"
              style={{ width: '100%', maxWidth: 432 }}
            >
              <Text
                key="toolset-oauth-error-hint"
                as="p"
                size="3"
                style={{
                  margin: 0,
                  color: 'var(--gray-10)',
                  lineHeight: '24px',
                  wordBreak: 'break-word',
                }}
              >
                {errorRetryHint}
              </Text>
              {showErrorDetail ? (
                <Text
                  key="toolset-oauth-error-detail"
                  as="p"
                  size="2"
                  style={{
                    margin: 0,
                    color: 'var(--gray-11)',
                    lineHeight: '20px',
                    wordBreak: 'break-word',
                  }}
                >
                  {error}
                </Text>
              ) : null}
            </Flex>
          </Flex>
          <Button key="toolset-oauth-error-retry" size="2" variant="outline" color="gray" onClick={closeWindow}>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 'var(--space-2)',
              }}
            >
              <MaterialIcon name="replay" size={16} color="var(--gray-11)" />
              {t('agentBuilder.toolsetOAuthTryAgain')}
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
      <Box key={status} style={columnStyle}>
        {statusPanel}
      </Box>
    </Flex>
  );
}
