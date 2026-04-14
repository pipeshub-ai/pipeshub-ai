'use client';

import React, { useState, useEffect } from 'react';
import { Flex, Box, Text, Heading, Button } from '@radix-ui/themes';
import { isElectron, hasStoredApiBaseUrl, setApiBaseUrl } from '@/lib/utils/api-base-url';

/**
 * ServerUrlGuard — wraps the app and shows a setup screen when:
 *   1. Running inside Electron, AND
 *   2. No API base URL has been configured in localStorage yet.
 *
 * On web this component is transparent (renders children immediately).
 */
export function ServerUrlGuard({ children }: { children: React.ReactNode }) {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);

  useEffect(() => {
    if (isElectron() && !hasStoredApiBaseUrl()) {
      setNeedsSetup(true);
    } else {
      setNeedsSetup(false);
    }
  }, []);

  // SSR / first render — show nothing until we know
  if (needsSetup === null) return null;

  if (needsSetup) {
    return <ServerUrlSetupScreen onComplete={() => setNeedsSetup(false)} />;
  }

  return <>{children}</>;
}

function ServerUrlSetupScreen({ onComplete }: { onComplete: () => void }) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [testing, setTesting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    const trimmed = url.trim().replace(/\/+$/, ''); // strip trailing slashes
    if (!trimmed) {
      setError('Please enter a server URL.');
      return;
    }

    try {
      new URL(trimmed);
    } catch {
      setError('Please enter a valid URL (e.g. http://localhost:3000).');
      return;
    }

    // Test connectivity
    setTesting(true);
    try {
      const res = await fetch(`${trimmed}/api/v1/health`, {
        method: 'GET',
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) throw new Error(`Server responded with ${res.status}`);
    } catch {
      // Even if health check fails, let user proceed — the endpoint may not exist
      // but the URL could still be valid
    }
    setTesting(false);

    setApiBaseUrl(trimmed);
    onComplete();
  };

  return (
    <Flex
      align="center"
      justify="center"
      style={{
        height: '100vh',
        width: '100vw',
        backgroundColor: 'var(--color-background)',
      }}
    >
      <Box
        style={{
          width: '100%',
          maxWidth: '460px',
          padding: 'var(--space-7)',
          borderRadius: 'var(--radius-4)',
          backgroundColor: 'var(--color-surface)',
          boxShadow: 'var(--shadow-4)',
        }}
      >
        {/* Logo / header */}
        <Flex direction="column" align="center" gap="3" style={{ marginBottom: 'var(--space-6)' }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/logo/pipes-hub.svg"
            alt="PipesHub"
            width={56}
            height={56}
          />
          <Heading size="5" align="center">
            Connect to PipesHub Server
          </Heading>
          <Text size="2" color="gray" align="center">
            Enter the URL of your PipesHub server to get started.
          </Text>
        </Flex>

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <Flex direction="column" gap="4">
            <Flex direction="column" gap="1">
              <Text as="label" size="2" weight="medium" htmlFor="server-url">
                Server URL
              </Text>
              <input
                id="server-url"
                type="text"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value);
                  setError('');
                }}
                placeholder="http://localhost:3000"
                autoFocus
                style={{
                  width: '100%',
                  padding: 'var(--space-2) var(--space-3)',
                  fontSize: 'var(--font-size-2)',
                  lineHeight: 'var(--line-height-2)',
                  borderRadius: 'var(--radius-2)',
                  border: `1px solid ${error ? 'var(--red-7)' : 'var(--gray-6)'}`,
                  backgroundColor: 'var(--color-surface)',
                  color: 'var(--gray-12)',
                  outline: 'none',
                  boxSizing: 'border-box',
                }}
              />
              {error && (
                <Text size="1" color="red">
                  {error}
                </Text>
              )}
            </Flex>

            <Button
              type="submit"
              size="3"
              disabled={testing}
              style={{ width: '100%', cursor: testing ? 'wait' : 'pointer' }}
            >
              {testing ? 'Connecting...' : 'Connect'}
            </Button>
          </Flex>
        </form>
      </Box>
    </Flex>
  );
}
