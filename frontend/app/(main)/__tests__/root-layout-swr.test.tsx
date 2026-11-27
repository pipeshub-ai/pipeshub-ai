/**
 * The app-wide SWR settings in root-layout.tsx, through the real apiClient (axios adapter and the
 * refresh `fetch` faked). Only the refresh decides the session: a 401 that reaches a hook signs nobody out.
 * Everything RootLayout renders besides SWRConfig is stubbed.
 */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { AxiosError, AxiosHeaders, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
import useSWR from 'swr';
import { installMemoryStorage, jsonResponse, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();
installMemoryStorage('sessionStorage');

const logoutAndRedirect = vi.fn();
const passthrough = ({ children }: { children?: React.ReactNode }) => <>{children}</>;
const nothing = () => null;
vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return {
    useAuthStore: auth.useAuthStore,
    logoutAndRedirect: () => {
      logoutAndRedirect();
      auth.useAuthStore.getState().logout();
    },
    AppLayoutExtras: nothing,
    BrandProvider: passthrough,
  };
});
vi.mock('@/app/components/ui/icon-font-script', () => ({ IconFontScript: nothing }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: nothing }));
vi.mock('@/app/components/theme-provider', () => ({ ThemeProvider: passthrough, ThemeScript: nothing }));
vi.mock('@/app/components/upload-progress-tracker', () => ({ UploadProgressTracker: nothing }));
vi.mock('@/app/components/feedback', () => ({ ToastContainer: nothing }));
vi.mock('@/app/(main)/components/user-profile-initializer', () => ({ UserProfileInitializer: nothing }));
vi.mock('@/app/(main)/components/org-profile-initializer', () => ({ OrgProfileInitializer: nothing }));
vi.mock('@/app/(main)/components/electron-local-sync-bootstrap', () => ({ ElectronLocalSyncBootstrap: nothing }));
vi.mock('@/app/(main)/components/surveys/user-background', () => ({ UserBackgroundSurvey: nothing }));
vi.mock('@/app/(main)/components/tours/onboarding', () => ({ OnboardingTour: nothing }));
vi.mock('@/app/(main)/onboarding/use-onboarding-redirect', () => ({ useOnboardingRedirect: () => {} }));
vi.mock('@/lib/store/sidebar-width-store', () => ({ useAppliedSidebarWidth: () => 240 }));
vi.mock('@/app/components/ui/auth-guard', () => ({ AuthGuard: passthrough }));
vi.mock('@/app/components/ui/health-gate', () => ({ HealthGate: passthrough }));
vi.mock('@/lib/store/auth-hydrator', () => ({ AuthHydrator: nothing }));
vi.mock('@/lib/navigation', () => ({ OrgUrlCleaner: nothing }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (select: (s: unknown) => unknown) => select({ profile: { fullName: 'Ada' }, updateProfile: () => {} }),
  selectIsProfileInitialized: () => true,
}));
vi.mock('@/app/(main)/components/full-name-dialog', () => ({ FullNameDialog: nothing }));
vi.mock('@/app/components/electron/server-url-setup', () => ({ ServerUrlGuard: passthrough }));
vi.mock('@/app/(main)/notifications/websocket-manager', () => ({ NotificationProvider: nothing }));
vi.mock('@/app/(main)/notifications/panel', () => ({ NotificationsPanel: nothing }));
vi.mock('@/app/components/sidebar/sidebar-expand-button', () => ({ SidebarExpandButton: nothing }));
vi.mock('@/app/components/sidebar/use-nav-hidden', () => ({ useNavHidden: () => false }));
vi.mock('@/app/(main)/components/mobile-menu-button', () => ({ MobileMenuButton: nothing }));
vi.mock('@/lib/harness-jobs/use-inbox-live', () => ({ useInboxLive: () => {} }));
vi.mock('@/lib/store/language-store', () => ({ useLanguageStore: (select: (s: unknown) => unknown) => select({ language: 'en' }) }));

const { useAuthStore } = await import('@/lib/store/auth-store');
const { apiClient } = await import('@/lib/api/axios-instance');
const scheduler = await import('@/lib/api/token-refresh-scheduler');
const { default: RootLayout } = await import('@/app/(main)/root-layout');

type FakeReply = { status: number; data?: unknown };

/** Answers each request from `replies` in order and records what was sent. */
function fakeServer(...replies: FakeReply[]) {
  const sent: InternalAxiosRequestConfig[] = [];
  const adapter: AxiosAdapter = async (config) => {
    sent.push(config);
    const reply = replies.shift() ?? { status: 200, data: {} };
    const response = { data: reply.data ?? {}, status: reply.status, statusText: '', headers: new AxiosHeaders({}), config };
    if (reply.status >= 200 && reply.status < 300) return response;
    throw new AxiosError(`Request failed with status code ${reply.status}`, 'ERR_BAD_RESPONSE', config, null, response);
  };
  apiClient.defaults.adapter = adapter;
  return sent;
}

const fetchMock = vi.fn<typeof fetch>();
const tick = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const notYourUser = { error: { code: 'UNAUTHORIZED', message: 'Unauthorized to update the user' } };

/** A hook that uses the provider's retry setting, with a short interval so a retry would show within the test. */
function Probe({ url }: { url: string }) {
  const { error } = useSWR(url, { errorRetryInterval: 5, dedupingInterval: 0 });
  return <span>{error ? 'error' : 'ok'}</span>;
}
const renderProbe = (url: string) =>
  render(
    <RootLayout sidebar={null}>
      <Probe url={url} />
    </RootLayout>,
  );

beforeEach(() => {
  fetchMock.mockReset();
  logoutAndRedirect.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), isAuthenticated: true });
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  scheduler.cancel();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the app-wide SWR settings', () => {
  it('keep the person signed in when a GET is answered 401 again after a successful refresh, and do not retry it', async () => {
    // Every retry would cost a refresh, so the refresh always succeeds here and the server always refuses.
    fetchMock.mockImplementation(async () => jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    const sent = fakeServer(...Array.from({ length: 20 }, () => ({ status: 401, data: notYourUser })));

    const view = renderProbe('/api/v1/users/u2');
    await view.findByText('error');
    await tick(150);

    expect(sent).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it('do not sign out on a 403, and still retry it', async () => {
    const adminOnly = { status: 403, data: { error: { message: 'Admin access required' } } };
    const sent = fakeServer(adminOnly, adminOnly);
    const view = renderProbe('/api/v1/admin-only');
    await view.findByText('error');
    await tick(150);
    expect(sent.length).toBeGreaterThanOrEqual(2);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('still retry a busy answer (503)', async () => {
    const sent = fakeServer({ status: 503 }, { status: 503 }, { status: 200, data: { ok: 1 } });
    renderProbe('/api/v1/busy');
    await tick(200);
    expect(sent.length).toBeGreaterThanOrEqual(2);
  });
});
