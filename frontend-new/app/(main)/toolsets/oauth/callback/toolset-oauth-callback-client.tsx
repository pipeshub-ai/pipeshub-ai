'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Spinner, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
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
            t('agentBuilder.toolsetOAuthFailedTitle');
          throw new Error(t('agentBuilder.toolsetOAuthProviderError', { error: providerMessage }));
        }
        if (!code) {
          throw new Error(t('agentBuilder.toolsetOAuthNoCode'));
        }
        if (!state) {
          throw new Error(t('agentBuilder.toolsetOAuthNoState'));
        }

        setMessage(t('agentBuilder.toolsetOAuthProcessing'));

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
        const detail = userFacingCallbackError(e, t('agentBuilder.toolsetOAuthFailedTitle'));
        setStatus('error');
        setError(detail);
        setMessage(t('agentBuilder.toolsetOAuthFailedTitle'));
        postToolsetOAuthErrorToOpener(toolsetSlug, detail);
        setTimeout(closeWindow, 400);
      }
    };

    void run();
  }, [closeWindow, t]);

  return (
    <Flex
      align="center"
      justify="center"
      style={{ minHeight: '100vh', padding: 'var(--space-5)', backgroundColor: 'var(--slate-2)' }}
    >
      <Box style={{ maxWidth: 420, width: '100%', textAlign: 'center' }}>
        {status === 'processing' && (
          <>
            <Flex align="center" justify="center" style={{ marginBottom: 'var(--space-4)' }}>
              <Spinner size="3" />
            </Flex>
            <Text size="4" weight="medium" style={{ display: 'block', marginBottom: 'var(--space-2)' }}>
              {message || t('agentBuilder.toolsetOAuthProcessing')}
            </Text>
            <Text size="2" color="gray">
              {t('agentBuilder.toolsetOAuthProcessingHint')}
            </Text>
          </>
        )}

        {status === 'success' && (
          <>
            <Flex align="center" justify="center" style={{ marginBottom: 'var(--space-4)' }}>
              <Box
                style={{
                  width: 72,
                  height: 72,
                  borderRadius: '999px',
                  backgroundColor: 'var(--green-3)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <MaterialIcon name="check_circle" size={40} color="var(--green-11)" />
              </Box>
            </Flex>
            <Text size="5" weight="bold" color="green" style={{ display: 'block', marginBottom: 'var(--space-2)' }}>
              {t('agentBuilder.toolsetOAuthSuccessTitle')}
            </Text>
            <Text size="2" color="gray" style={{ display: 'block', marginBottom: 'var(--space-4)' }}>
              {message}
            </Text>
            <Button size="2" variant="solid" onClick={closeWindow}>
              {t('agentBuilder.toolsetOAuthCloseWindow')}
            </Button>
          </>
        )}

        {status === 'error' && (
          <>
            <Flex align="center" justify="center" style={{ marginBottom: 'var(--space-4)' }}>
              <Box
                style={{
                  width: 72,
                  height: 72,
                  borderRadius: '999px',
                  backgroundColor: 'var(--red-3)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <MaterialIcon name="error" size={40} color="var(--red-11)" />
              </Box>
            </Flex>
            <Text size="5" weight="bold" color="red" style={{ display: 'block', marginBottom: 'var(--space-2)' }}>
              {t('agentBuilder.toolsetOAuthFailedTitle')}
            </Text>
            <Text
              size="2"
              color="red"
              style={{
                display: 'block',
                marginBottom: 'var(--space-4)',
                textAlign: 'left',
                padding: 'var(--space-3)',
                borderRadius: 'var(--radius-3)',
                backgroundColor: 'var(--red-2)',
              }}
            >
              {error}
            </Text>
            <Button size="2" variant="solid" color="gray" onClick={closeWindow}>
              {t('agentBuilder.toolsetOAuthCloseWindow')}
            </Button>
          </>
        )}
      </Box>
    </Flex>
  );
}
