'use client';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { apiClient } from '@/lib/api';
import { isProcessedError } from '@/lib/api';

// ============================================================================
// Helpers
// ============================================================================

function userFacingError(e: unknown, fallback: string): string {
  if (isProcessedError(e)) return e.message;
  if (e instanceof Error && e.message.trim()) return e.message;
  return fallback;
}

type CallbackStatus = 'processing' | 'success' | 'error';

// ============================================================================
// Component
// ============================================================================

/**
 * Completes MCP server OAuth in a full-page redirect flow:
 * - Reads `code`, `state`, `error` from the URL
 * - Calls GET /api/v1/mcp-servers/oauth/callback
 * - Shows processing / success / error, then redirects to /workspace/mcp-servers
 *
 * Uses `callbackFiredRef` to guard against StrictMode double-fire since the
 * backend consumes the OAuth state on the first call.
 */
export function McpOAuthCallbackClient() {
  const router = useRouter();
  const [status, setStatus] = useState<CallbackStatus>('processing');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const ranRef = useRef(false);

  const goToMcpServers = useCallback(() => {
    const returnTo = sessionStorage.getItem('mcp_oauth_return_to');
    if (returnTo) {
      sessionStorage.removeItem('mcp_oauth_return_to');
      router.replace(returnTo);
    } else {
      router.replace('/workspace/mcp-servers/?tab=my-servers');
    }
  }, [router]);

  useEffect(() => {
    if (ranRef.current) return;
    ranRef.current = true;

    const run = async () => {
      const searchParams = new URLSearchParams(window.location.search);
      const code = searchParams.get('code');
      const state = searchParams.get('state');
      const oauthError = searchParams.get('error');
      const oauthErrorDesc = searchParams.get('error_description');

      try {
        if (oauthError) {
          const providerMessage =
            oauthErrorDesc?.trim() || oauthError || 'OAuth provider error';
          throw new Error(`OAuth error: ${providerMessage}`);
        }

        if (!code) {
          throw new Error('No authorization code received');
        }

        if (!state) {
          throw new Error('No state parameter received');
        }

        setMessage('Processing OAuth authentication...');

        await apiClient.get('/api/v1/mcp-servers/oauth/callback', {
          params: {
            code,
            state,
            error: oauthError ?? undefined,
            baseUrl: window.location.origin,
          },
        });

        setStatus('success');
        setMessage('Authentication successful! Redirecting to MCP Servers...');

        setTimeout(() => {
          goToMcpServers();
        }, 2000);
      } catch (e) {
        const detail = userFacingError(e, 'OAuth authentication failed. Please try again.');
        setStatus('error');
        setError(detail);
        setMessage('Authentication failed');
      }
    };

    void run();
  }, [goToMcpServers]);

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
              style={{
                margin: 0,
                color: 'var(--gray-12)',
                letterSpacing: '-0.04px',
                lineHeight: '26px',
              }}
            >
              Completing sign-in...
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
              {message || 'Please wait while we complete your authentication.'}
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
              Authentication Successful
            </Text>
            <Text
              as="p"
              size="3"
              style={{ margin: 0, color: 'var(--gray-10)', lineHeight: '24px' }}
            >
              {message}
            </Text>
          </Flex>
          <Button size="2" variant="solid" onClick={goToMcpServers}>
            Continue
          </Button>
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
          <Flex direction="column" align="center" gap="1" style={{ width: '100%' }}>
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
              Authentication Failed
            </Text>
            {error && (
              <Text
                as="p"
                size="2"
                style={{
                  margin: 0,
                  color: 'var(--gray-11)',
                  lineHeight: '20px',
                  wordBreak: 'break-word',
                  maxWidth: 432,
                }}
              >
                {error}
              </Text>
            )}
          </Flex>
          <Button
            size="2"
            variant="outline"
            color="gray"
            onClick={goToMcpServers}
          >
            <MaterialIcon name="arrow_back" size={16} color="var(--gray-11)" />
            Go Back
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
