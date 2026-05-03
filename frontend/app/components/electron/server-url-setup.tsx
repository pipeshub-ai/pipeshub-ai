'use client';

import React, { useState, useLayoutEffect } from 'react';
import { Flex, Box, Text, Heading, Button } from '@radix-ui/themes';
import { isElectron, getApiBaseUrl, setApiBaseUrl } from '@/lib/utils/api-base-url';
import { LoadingScreen } from '@/app/components/ui/auth-guard';

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

  // useLayoutEffect (not useEffect) so we commit the real route before the
  // first browser paint. A plain effect runs after paint — on full navigations
  // (logout → /login, or after URL setup "Continue") users briefly saw a blank
  // white screen because we returned null until the effect ran.
  useLayoutEffect(() => {
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

  // Until we know Electron vs web and launch confirmation — match AuthGuard
  // loading chrome instead of returning null (which flashes white).
  if (needsSetup === null) return <LoadingScreen />;

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

    let parsed: URL;
    try {
      parsed = new URL(trimmed);
    } catch {
      setError('Please enter a valid URL (e.g. http://localhost:3000).');
      return;
    }

    // Reject javascript:, file:, data:, etc. The stored value is later used as
    // axios.baseURL and string-concatenated into fetch() URLs, so anything
    // other than http(s) is unsafe.
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      setError('Server URL must use http:// or https://.');
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
