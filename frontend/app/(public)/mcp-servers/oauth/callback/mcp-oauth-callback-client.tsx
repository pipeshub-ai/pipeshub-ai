'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { McpServersApi } from '@/app/(main)/workspace/mcp-servers/api';
import { isProcessedError } from '@/lib/api';
import {
  postMcpOAuthErrorToOpener,
  postMcpOAuthSuccessToOpener,
} from '@/app/(main)/workspace/mcp-servers/oauth/mcp-oauth-window-messages';

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
 * Completes MCP server OAuth in a popup: exchanges `code`+`state` via the Node proxy
 * (`GET /api/v1/mcp-servers/oauth/callback`), notifies the opener with
 * `MCP_OAUTH_SUCCESS` / `MCP_OAUTH_ERROR`, then closes the window.
 */
export function McpOAuthCallbackClient() {
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
      try {
        const searchParams = new URLSearchParams(window.location.search);
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const oauthError = searchParams.get('error');
        const oauthErrorDescription = searchParams.get('error_description');

        if (oauthError) {
          const providerMessage =
            oauthErrorDescription?.trim() || oauthError || t('workspace.mcpServers.oauth.retryHint');
          throw new Error(t('workspace.mcpServers.oauth.providerError', { error: providerMessage }));
        }
        if (!code) {
          throw new Error(t('workspace.mcpServers.oauth.noCode'));
        }
        if (!state) {
          throw new Error(t('workspace.mcpServers.oauth.noState'));
        }

        const data = await McpServersApi.completeOAuthCallback({ code, state });

        if (!data?.success) {
          const friendlyMessage = data?.errorMessage;
          throw new Error(
            typeof friendlyMessage === 'string' && friendlyMessage.trim()
              ? friendlyMessage
              : t('workspace.mcpServers.oauth.backendRejected')
          );
        }

        setStatus('success');
        setMessage(t('workspace.mcpServers.oauth.successBody'));
        setTimeout(() => {
          postMcpOAuthSuccessToOpener(data.instanceId ?? null);
          closeWindow();
        }, 900);
      } catch (e) {
        const detail = userFacingCallbackError(e, t('workspace.mcpServers.oauth.retryHint'));
        setStatus('error');
        setError(detail);
        postMcpOAuthErrorToOpener(detail);
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

  let statusPanel: ReactNode;
  switch (status) {
    case 'processing':
      statusPanel = (
        <Flex direction="column" align="center" gap="5">
          <Box style={{ width: 48, height: 48, flexShrink: 0 }}>
            <LottieLoader variant="still" size={48} />
          </Box>
          <Flex direction="column" align="center" gap="1">
            <Text as="p" size="4" weight="medium" style={{ margin: 0, color: 'var(--gray-12)', letterSpacing: '-0.04px', lineHeight: '26px' }}>
              {t('workspace.mcpServers.oauth.completingTitle')}
            </Text>
            <Text as="p" size="3" style={{ margin: 0, color: 'var(--gray-10)', lineHeight: '24px', maxWidth: 432 }}>
              {t('workspace.mcpServers.oauth.processingHint')}
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
          <Flex direction="column" align="center" gap="1">
            <Text as="p" size="4" weight="medium" style={{ margin: 0, color: 'var(--accent-11)', letterSpacing: '-0.04px', lineHeight: '26px' }}>
              {t('workspace.mcpServers.oauth.successTitle')}
            </Text>
            <Text as="p" size="3" style={{ margin: 0, color: 'var(--gray-10)', lineHeight: '24px' }}>
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
            <Text as="p" size="4" weight="medium" style={{ margin: 0, color: 'var(--red-10)', letterSpacing: '-0.04px', lineHeight: '26px' }}>
              {t('workspace.mcpServers.oauth.failedTitle')}
            </Text>
            <Text as="p" size="3" style={{ margin: 0, color: 'var(--gray-10)', lineHeight: '24px', wordBreak: 'break-word' }}>
              {error}
            </Text>
          </Flex>
          <Button size="2" variant="outline" color="gray" onClick={closeWindow}>
            <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 'var(--space-2)' }}>
              <MaterialIcon name="close" size={16} color="var(--gray-11)" />
              {t('common.close')}
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
