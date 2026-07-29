/**
 * Regression test for the Ask User Tool Improvement Plan (Phase 3):
 * `has_ui_client` on the Python side — which gates every UI-only tool's SSE
 * emission, including `ask_user_question` (see
 * `backend/python/app/agents/agent_loop/hooks/ask_user_question.py`) — is
 * derived entirely from the presence of the `client-name` request header
 * (`backend/python/app/api/routes/chatbot.py`). `streamSSERequest` is the
 * ONLY place that header gets attached for every streaming chat call. If a
 * future refactor of this function drops it, every UI-only tool silently
 * no-ops with no error surfaced anywhere — this test exists so that
 * regression fails loudly here instead.
 */
import { describe, it, expect, vi } from 'vitest';

vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: {
    getState: () => ({ accessToken: 'test-access-token' }),
  },
  logoutAndRedirect: vi.fn(),
}));

vi.mock('../token-refresh', () => ({
  isTokenExpired: () => false,
  isRefreshInProgress: () => false,
  refreshAccessToken: vi.fn(),
}));

vi.mock('@/lib/electron', () => ({
  isElectron: () => false,
  streamingFetch: vi.fn(),
}));

vi.mock('@/lib/utils/api-base-url', () => ({
  getApiBaseUrl: () => '',
}));

vi.mock('@/lib/utils/request-id', () => ({
  generateRequestId: () => 'test-request-id',
}));

import { streamSSERequest } from '../streaming';

function mockEmptyStreamResponse(): Response {
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => ({ done: true, value: undefined }),
      }),
    },
  } as unknown as Response;
}

describe('streamSSERequest header contract', () => {
  it('sends the lowercase client-name header every streaming chat call relies on', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockEmptyStreamResponse());
    vi.stubGlobal('fetch', fetchMock);

    await streamSSERequest(
      '/api/v1/chat/stream',
      { query: 'hi', chatMode: 'internal_search' },
      { onEvent: vi.fn(), onError: vi.fn() },
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, requestInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = requestInit.headers as Record<string, string>;

    // Exact value, not just presence: Node.js's `sanitizeHeaders` allowlist
    // (`backend/nodejs/apps/src/libs/commands/command.interface.ts`) and
    // Python's `client_name = request.headers.get("client-name")`
    // (`backend/python/app/api/routes/chatbot.py`) both match on this exact
    // lowercase key — a rename to `Client-Name`/`X-Client-Name` would pass
    // a looser "header exists" check but still break the contract.
    expect(headers['client-name']).toBe('pipeshub-ai');
  });

  it('sends client-name regardless of chatMode', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockEmptyStreamResponse());
    vi.stubGlobal('fetch', fetchMock);

    await streamSSERequest(
      '/api/v1/chat/stream',
      { query: 'hi', chatMode: 'quick' },
      { onEvent: vi.fn(), onError: vi.fn() },
    );

    const [, requestInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = requestInit.headers as Record<string, string>;
    expect(headers['client-name']).toBe('pipeshub-ai');
  });
});
