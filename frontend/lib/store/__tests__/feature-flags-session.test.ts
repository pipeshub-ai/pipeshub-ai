/**
 * Feature flags belong to the org signed in to. At session end they are
 * forgotten, so the next sign-in (maybe to another server on the desktop app)
 * waits for its own instead of using these. The request goes through the real
 * apiClient, whose session guard drops an answer that lands after sign-out.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { AxiosHeaders, AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { apiClient } = await import('@/lib/api/axios-instance');
const { useFeatureFlagsStore, selectFeatureFlagsLoaded } = await import('../feature-flags-store');
const { clearSessionCache } = await import('@/lib/api/session-cache');

const pending: Array<(status: number, flags?: Record<string, boolean>) => void> = [];
function deferredAdapter(config: InternalAxiosRequestConfig) {
  return new Promise<AxiosResponse>((resolve, reject) => {
    pending.push((status, flags = {}) => {
      const response = { data: { featureFlags: flags }, status, statusText: '', headers: new AxiosHeaders(), config };
      if (status < 400) resolve(response);
      else reject(new AxiosError('failed', 'ERR_BAD_RESPONSE', config, null, response));
    });
  });
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
}

const signIn = () => useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r', isAuthenticated: true });
const signOut = () => useAuthStore.getState().logout();
const loaded = () => selectFeatureFlagsLoaded(useFeatureFlagsStore.getState());

beforeEach(async () => {
  signOut();
  signIn();
  pending.length = 0;
  apiClient.defaults.adapter = deferredAdapter;
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('feature flags at session end', () => {
  it('are forgotten, so the next sign-in waits for its own', async () => {
    const load = useFeatureFlagsStore.getState().fetchFlags();
    await flush();
    pending[0](200, { ENABLE_PROJECTS: true });
    await load;
    expect(useFeatureFlagsStore.getState().flags).toEqual({ ENABLE_PROJECTS: true });

    signOut();

    expect(loaded()).toBe(false);
  });

  it('are fetched again after session end, even while the last fetch is still out', async () => {
    void useFeatureFlagsStore.getState().fetchFlags();
    await flush();

    signOut();
    signIn();
    void useFeatureFlagsStore.getState().fetchFlags();
    await flush();

    expect(pending).toHaveLength(2);
  });

  it("are not taken from the last session's answer", async () => {
    void useFeatureFlagsStore.getState().fetchFlags();
    await flush();

    signOut();
    pending[0](200, { ENABLE_PROJECTS: true });
    await flush();

    expect(useFeatureFlagsStore.getState().flags).toBeNull();
  });

  it("are not marked loaded by the last session's failure", async () => {
    void useFeatureFlagsStore.getState().fetchFlags();
    await flush();

    signOut();
    pending[0](500);
    await flush();

    expect(loaded()).toBe(false);
    expect(useFeatureFlagsStore.getState().loading).toBe(false);
  });

  it("keep the new session's fetch shared when the last session's answer lands", async () => {
    void useFeatureFlagsStore.getState().fetchFlags();
    await flush();
    signOut();
    signIn();
    void useFeatureFlagsStore.getState().fetchFlags();
    await flush();

    pending[0](200, { OLD: true });
    await flush();
    void useFeatureFlagsStore.getState().fetchFlags();
    await flush();

    expect(pending).toHaveLength(2);
    pending[1](200, { NEW: true });
    await flush();
    expect(useFeatureFlagsStore.getState().flags).toEqual({ NEW: true });
  });

  it('are loaded again for the new org when the session ends on an org switch (app stays open)', async () => {
    const load = useFeatureFlagsStore.getState().fetchFlags();
    await flush();
    pending[0](200, { ENABLE_PROJECTS: true });
    await load;

    clearSessionCache({ refetch: true });
    expect(loaded()).toBe(false);
    await flush();

    expect(pending).toHaveLength(2);
    pending[1](200, { ENABLE_PROJECTS: false });
    await flush();
    expect(useFeatureFlagsStore.getState().flags).toEqual({ ENABLE_PROJECTS: false });
  });

  it('a refresh within the session (Labs save) still updates them', async () => {
    const first = useFeatureFlagsStore.getState().fetchFlags();
    await flush();
    pending[0](200, { ENABLE_SKILLS: true });
    await first;

    const refresh = useFeatureFlagsStore.getState().fetchFlags();
    await flush();
    pending[1](200, { ENABLE_SKILLS: false });
    await refresh;

    expect(useFeatureFlagsStore.getState().flags).toEqual({ ENABLE_SKILLS: false });
  });
});
