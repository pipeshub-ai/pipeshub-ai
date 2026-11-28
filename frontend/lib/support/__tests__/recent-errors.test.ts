import { beforeEach, describe, expect, it, vi } from 'vitest';

type Rejected = (error: unknown) => Promise<never>;
type AuthState = { accessToken: string | null };

const mocks = vi.hoisted(() => ({
  handlers: [] as Array<(error: unknown) => Promise<never>>,
  authListeners: [] as Array<(state: unknown, previous: unknown) => void>,
}));

vi.mock('@/lib/api', () => ({
  apiClient: { interceptors: { response: { use: (_ok: unknown, rejected: Rejected) => mocks.handlers.push(rejected) } } },
}));

// The recorder reads the auth store through `@/config`, whichever store a build binds there.
vi.mock('@/config', () => ({
  useAuthStore: {
    subscribe: (listener: (state: unknown, previous: unknown) => void) => mocks.authListeners.push(listener),
  },
}));

import {
  clearRecentErrors,
  getRecentErrors,
  installRecentErrorRecorder,
  recordFailedRequest,
} from '../recent-errors';

function failed(
  url: string,
  status: number | undefined,
  options: { method?: string; code?: string; data?: unknown; config?: Record<string, unknown> } = {},
) {
  return {
    isAxiosError: true,
    code: options.code,
    config: { url, method: options.method ?? 'get', headers: {}, ...options.config },
    response: status === undefined ? undefined : { status, data: options.data },
  };
}

describe('recent error recorder', () => {
  beforeEach(() => {
    clearRecentErrors();
  });

  it('keeps the path without its query string, the status and the request id', () => {
    recordFailedRequest(
      failed('/api/v1/connectors/c1/sync?token=secret#frag', 502, {
        method: 'post',
        data: { error: { requestId: 'u123-V1StGXR8_Z5jdHi6B-myT' } },
      }),
    );

    const [entry] = getRecentErrors();
    expect(entry).toMatchObject({
      method: 'POST',
      path: '/api/v1/connectors/c1/sync',
      status: 502,
      requestId: 'u123-V1StGXR8_Z5jdHi6B-myT',
    });
    expect(JSON.stringify(entry)).not.toContain('secret');
  });

  it('records network failures and timeouts as status 0', () => {
    recordFailedRequest(failed('/api/v1/knowledgeBase', undefined, { code: 'ERR_NETWORK' }));
    expect(getRecentErrors()[0]).toMatchObject({ status: 0, path: '/api/v1/knowledgeBase' });
  });

  it('ignores cancelled calls, 401s and the sign-in, account, support and feedback endpoints', () => {
    recordFailedRequest(failed('/api/v1/agents', undefined, { code: 'ERR_CANCELED' }));
    recordFailedRequest(failed('/api/v1/agents', 401));
    recordFailedRequest(failed('/api/v1/auth/token/refresh', 500));
    recordFailedRequest(failed('/api/v1/userAccount/authenticate', 500));
    recordFailedRequest(failed('/api/v1/support', 500, { method: 'post' }));
    recordFailedRequest(failed('/api/v1/feedback', 500, { method: 'post' }));

    expect(getRecentErrors()).toEqual([]);
  });

  it('skips a 4xx the caller handles in place, and keeps one the app reported', () => {
    // An empty search answers 404 and the search box shows "no results".
    recordFailedRequest(failed('/api/v1/search', 404, { config: { suppressErrorToast: true } }));
    recordFailedRequest(failed('/api/v1/connectors/c1', 403));

    expect(getRecentErrors().map((entry) => [entry.path, entry.status])).toEqual([['/api/v1/connectors/c1', 403]]);
  });

  it('always keeps server errors, even from a call that shows its own message', () => {
    recordFailedRequest(failed('/api/v1/connectors/c1/sync', 503, { config: { suppressErrorToast: true } }));
    recordFailedRequest(failed('/api/v1/chat', 429, { config: { suppressErrorToast: true } }));

    expect(getRecentErrors().map((entry) => entry.status)).toEqual([503, 429]);
  });

  it('counts one failure once when it passes the recorder twice', () => {
    // A call retried after a token refresh rejects through both the retry and the original chain.
    const error = failed('/api/v1/agents', 500);
    recordFailedRequest(error);
    recordFailedRequest({ originalError: error });

    expect(getRecentErrors()).toHaveLength(1);
  });

  it('keeps only the five most recent', () => {
    for (let i = 0; i < 7; i += 1) recordFailedRequest(failed(`/api/v1/agents/${String(i)}`, 500));

    expect(getRecentErrors().map((entry) => entry.path)).toEqual([
      '/api/v1/agents/2',
      '/api/v1/agents/3',
      '/api/v1/agents/4',
      '/api/v1/agents/5',
      '/api/v1/agents/6',
    ]);
  });

  it('drops entries older than half an hour', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-10-09T10:00:00Z'));
      recordFailedRequest(failed('/api/v1/agents/old', 500));
      vi.setSystemTime(new Date('2026-10-09T10:31:00Z'));
      recordFailedRequest(failed('/api/v1/agents/new', 500));

      expect(getRecentErrors().map((entry) => entry.path)).toEqual(['/api/v1/agents/new']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('watches the API client once, passes each error on, and forgets everything on sign-out', async () => {
    installRecentErrorRecorder();
    installRecentErrorRecorder();
    expect(mocks.handlers).toHaveLength(1);
    expect(mocks.authListeners).toHaveLength(1);

    const error = failed('/api/v1/agents', 500);
    await expect(mocks.handlers[0](error)).rejects.toBe(error);
    expect(getRecentErrors()).toHaveLength(1);

    const signedIn: AuthState = { accessToken: 'token' };
    mocks.authListeners[0]({ accessToken: 'refreshed' }, signedIn);
    expect(getRecentErrors()).toHaveLength(1);

    mocks.authListeners[0]({ accessToken: null }, signedIn);
    expect(getRecentErrors()).toEqual([]);
  });
});
