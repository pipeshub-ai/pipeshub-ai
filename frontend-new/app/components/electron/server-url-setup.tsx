'use client';

import React, { useState, useEffect } from 'react';
import { Flex, Box, Text, Heading, Button } from '@radix-ui/themes';
import { isElectron, getApiBaseUrl, setApiBaseUrl } from '@/lib/utils/api-base-url';

// Launch-scoped flag: main process assigns one APP_LAUNCH_ID per app process;
// preload exposes it on every load. We persist the confirmed id in localStorage;
// when it matches we skip the prompt. Survives route groups, login redirects,
// and full page loads (preload re-runs but the ID stays stable).
const CONFIRMED_LAUNCH_KEY = 'PIPESHUB_URL_CONFIRMED_LAUNCH_ID';

function getLaunchId(): string | null {
  if (typeof window === 'undefined') return null;
  const api = (window as Window & { electronAPI?: { launchId?: string } }).electronAPI;
  const id = api?.launchId;
  return typeof id === 'string' && id.length > 0 ? id : null;
}

/**
 * ServerUrlGuard — wraps the app and shows a setup screen on every Electron
 * launch so the user can confirm or edit the PipesHub server URL. Any
 * previously stored URL is pre-filled and editable.
 *
 * On web this component is transparent (renders children immediately).
 */
export function ServerUrlGuard({ children }: { children: React.ReactNode }) {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);

  useEffect(() => {
    if (!isElectron()) {
      setNeedsSetup(false);
      return;
    }
    const launchId = getLaunchId();
    // Defensive: if preload didn't expose a launchId we can't distinguish
    // launches, so don't loop the prompt across route-group navigations —
    // just render children.
    if (!launchId) {
      setNeedsSetup(false);
      return;
    }
    const confirmedLaunch = localStorage.getItem(CONFIRMED_LAUNCH_KEY);
    setNeedsSetup(confirmedLaunch !== launchId);
  }, []);

  // SSR / first render — show nothing until we know
  if (needsSetup === null) return null;

  if (needsSetup) {
    return <ServerUrlSetupScreen onComplete={() => setNeedsSetup(false)} />;
  }

  return <>{children}</>;
}

function ServerUrlSetupScreen({ onComplete }: { onComplete: () => void }) {
  const existing = typeof window !== 'undefined' ? getApiBaseUrl() : '';
  const [url, setUrl] = useState(existing);
  const [error, setError] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    const trimmed = url.trim().replace(/\/+$/, '');
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

    setApiBaseUrl(trimmed);
    const launchId = getLaunchId();
    if (launchId) localStorage.setItem(CONFIRMED_LAUNCH_KEY, launchId);
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
            {existing
              ? 'Confirm or update your PipesHub server URL.'
              : 'Enter the URL of your PipesHub server to get started.'}
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
              style={{ width: '100%', cursor: 'pointer' }}
            >
              Connect
            </Button>
          </Flex>
        </form>
      </Box>
    </Flex>
  );
}
