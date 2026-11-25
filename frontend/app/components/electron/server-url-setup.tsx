'use client';

import React, { useState, useLayoutEffect, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Heading, Button } from '@radix-ui/themes';
import { requestElectronServerUrlChange } from '@/lib/store/auth-store';
import { useAuthStore, ELECTRON_SERVER_URL_NAVIGATION_EVENT } from '@/config';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import {
  isElectron,
  markServerMode,
  setLocalModeApiBaseUrl,
  setSessionApiBaseUrl,
  shouldSkipElectronServerUrlSetup,
} from '@/lib/electron';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import {
  decideDesktopGate,
  getDesktopApi,
  switchDesktopMode,
  useDesktopState,
  type DesktopState,
} from '@/lib/electron/desktop-mode';
import { getElectronApiBaseUrl } from '@/lib/electron/api-base-url-storage';
import { LocalStartScreen, ModeChoiceScreen } from './desktop-mode-screens';

/**
 * ServerUrlGuard — wraps the app in Electron until the user connects a server URL.
 * Connect saves a draft (localStorage) for pre-fill on reopen; login ack is set
 * only after successful sign-in. Without ack, quit + reopen shows this screen
 * again with the last URL pre-filled.
 *
 * `allowChangeServer` renders a small "Change server" affordance over the
 * guarded pre-auth screens (login/sign-up) so a typo'd or unreachable URL can
 * be corrected on the spot — without it, a bad URL leads straight to a login
 * screen that can only fail, with no way back short of quitting the app.
 * Only pass this on layouts reachable while signed out; once authenticated,
 * server changes go through the explicit workspace-menu logout instead.
 *
 * On web this component is transparent (renders children immediately).
 */
export function ServerUrlGuard({
  children,
  allowChangeServer = false,
}: {
  children: React.ReactNode;
  allowChangeServer?: boolean;
}) {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  // Desktop mode: the first-run choice, and the local server's lifecycle.
  const desktop = useDesktopState();
  const localModeRef = useRef(false);
  localModeRef.current = desktop.state?.mode === 'local';

  // useLayoutEffect (not useEffect) so we commit the real route before the
  // first browser paint. A plain effect runs after paint — on full navigations
  // (logout → /chat, or after URL setup "Continue") users briefly saw a blank
  // white screen because we returned null until the effect ran.
  //
  // The `isElectron()` check is inside the effect (not at the top of the
  // component) so the server-rendered HTML and the first client render both
  // produce <LoadingScreen />. That keeps the SSR/CSR trees identical under
  // Next's static export — `isElectron()` returns false in SSR but true at
  // runtime in Electron, and a top-level branch would mismatch on hydration.
  // Web users see no flash because the effect runs synchronously before paint.
  useLayoutEffect(() => {
    if (!isElectron()) {
      setNeedsSetup(false);
      return;
    }
    setNeedsSetup(!shouldSkipElectronServerUrlSetup());
  }, []);

  // Workspace logout clears the ack but keeps the last URL — re-show this screen
  // without remounting the layout (useLayoutEffect above only runs once).
  useEffect(() => {
    if (!isElectron()) return;
    const onWorkspaceLogout = (event: Event) => {
      if (localModeRef.current) {
        // Signing out of the local server stays on it; "Change server" leaves it.
        if ((event as CustomEvent).detail?.change) {
          void switchDesktopMode('remote', () => useAuthStore.getState().logout());
        }
        return;
      }
      setNeedsSetup(true);
    };
    window.addEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, onWorkspaceLogout);
    return () => {
      window.removeEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, onWorkspaceLogout);
    };
  }, []);

  if (needsSetup === null || !desktop.loaded) return <LoadingScreen />;

  const gate = isElectron()
    ? decideDesktopGate(desktop.state, !needsSetup)
    : 'ready';
  if (isElectron() && gate !== 'local') markServerMode();

  // Local mode: nothing of the signed-in app renders until the server answers,
  // and it is replaced by the error screen if the server later goes away.
  if (gate === 'local' && desktop.state) {
    return <LocalModeGate state={desktop.state}>{children}</LocalModeGate>;
  }

  if (gate === 'choose' && desktop.state) {
    return (
      <ModeChoiceScreen
        localAvailable={desktop.state.localAvailable}
        onChoose={(mode) => void getDesktopApi()?.setMode(mode)}
      />
    );
  }

  if (gate === 'setup' || needsSetup) {
    return (
      <ServerUrlSetupScreen
        onComplete={() => setNeedsSetup(false)}
        localAvailable={desktop.state?.localAvailable === true}
        onUseLocal={() => void switchDesktopMode('local', () => useAuthStore.getState().logout())}
      />
    );
  }

  return (
    <>
      {children}
      {allowChangeServer && isElectron() && <ChangeServerLink />}
    </>
  );
}

/**
 * Local mode: waits for the launcher to report a running server, points the
 * app at its loopback URL, and falls back to the progress/error screen if the
 * server stops. The port is chosen by the launcher at each start, so the URL
 * is taken from the status every time rather than saved.
 */
function LocalModeGate({ state, children }: { state: DesktopState; children: React.ReactNode }) {
  const { local } = state;
  if (local.state !== 'running' || !local.url) {
    // No server of this launch yet: requests (a token refresh too) wait instead of going to an earlier launch's port.
    setLocalModeApiBaseUrl(null);
    return <LocalStartScreen status={local} state={state} />;
  }
  // In memory only, and before the children's first request.
  if (getElectronApiBaseUrl() !== local.url) setLocalModeApiBaseUrl(local.url);
  return <>{children}</>;
}

/** Persistent escape hatch for pre-auth Electron screens — see ServerUrlGuard. */
function ChangeServerLink() {
  const { t } = useTranslation();

  return (
    <Button
      type="button"
      variant="ghost"
      size="1"
      onClick={() => requestElectronServerUrlChange()}
      style={{
        position: 'fixed',
        top: 'var(--space-4)',
        right: 'var(--space-4)',
        zIndex: 10,
        cursor: 'pointer',
      }}
    >
      {t('electron.serverUrlSetup.changeServer')}
    </Button>
  );
}

function ServerUrlSetupScreen({
  onComplete,
  localAvailable = false,
  onUseLocal,
}: {
  onComplete: () => void;
  localAvailable?: boolean;
  onUseLocal?: () => void;
}) {
  const { t } = useTranslation();
  const existing = typeof window !== 'undefined' ? getApiBaseUrl() : '';
  const [url, setUrl] = useState(existing);
  const [error, setError] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    const trimmed = url.trim().replace(/\/+$/, '');
    if (!trimmed) {
      setError(t('electron.serverUrlSetup.errors.empty'));
      return;
    }

    let parsed: URL;
    try {
      parsed = new URL(trimmed);
    } catch {
      setError(t('electron.serverUrlSetup.errors.invalid'));
      return;
    }

    // Reject javascript:, file:, data:, etc. The stored value is later used as
    // axios.baseURL and string-concatenated into fetch() URLs, so anything
    // other than http(s) is unsafe.
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      setError(t('electron.serverUrlSetup.errors.protocol'));
      return;
    }

    setSessionApiBaseUrl(trimmed);
    onComplete();
  };

  return (
    <Flex
      align="center"
      justify="center"
      className="viewport-height"
      style={{
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
          <img
            src="/logo/pipes-hub.svg"
            alt="PipesHub"
            width={56}
            height={56}
          />
          <Heading size="5" align="center">
            {t('electron.serverUrlSetup.title')}
          </Heading>
          <Text size="2" color="gray" align="center">
            {existing
              ? t('electron.serverUrlSetup.subtitleExisting')
              : t('electron.serverUrlSetup.subtitleFirstRun')}
          </Text>
        </Flex>

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <Flex direction="column" gap="4">
            <Flex direction="column" gap="1">
              <Text as="label" size="2" weight="medium" htmlFor="server-url">
                {t('electron.serverUrlSetup.label')}
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
              {t('electron.serverUrlSetup.submit')}
            </Button>
            {localAvailable && onUseLocal && (
              <Button
                type="button"
                variant="ghost"
                size="2"
                onClick={onUseLocal}
                style={{ cursor: 'pointer' }}
              >
                {t('electron.desktopMode.useLocalInstead')}
              </Button>
            )}
          </Flex>
        </form>
      </Box>
    </Flex>
  );
}
