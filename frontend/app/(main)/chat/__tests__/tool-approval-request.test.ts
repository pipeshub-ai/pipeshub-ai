/**
 * `buildStreamChatRequestForSlot` with an answer to a tool approval card.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// See reasoning-effort.test.ts: runtime.ts pulls in modules that hydrate auth from localStorage.
vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: { getState: () => ({ isHydrated: true }) },
  hydrateAuthStore: vi.fn(),
  LOGIN_NAVIGATION_EVENT: 'pipeshub:request-login-navigation',
}));

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), request: vi.fn() },
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  axiosFetcher: vi.fn(),
  publicFetcher: vi.fn(),
  configuredFetcher: vi.fn(),
  streamRequest: vi.fn(),
  createStreamController: vi.fn(),
  streamSSERequest: vi.fn(),
  processError: vi.fn(),
  ErrorType: {},
  isProcessedError: vi.fn(() => false),
  isRequestCancelledError: vi.fn(() => false),
  isSearchNoAccessibleDocumentsNotFound: vi.fn(() => false),
  SEARCH_ACCESSIBLE_RECORDS_NOT_FOUND_STATUS: 404,
  SEARCH_NO_ACCESSIBLE_DOCUMENTS_FRAGMENT: '',
  useMutation: vi.fn(),
  withToast: vi.fn(),
}));

function makeMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => (data.has(key) ? (data.get(key) as string) : null),
    setItem: (key: string, value: string) => data.set(key, String(value)),
    removeItem: (key: string) => {
      data.delete(key);
    },
    clear: () => data.clear(),
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    get length() {
      return data.size;
    },
  };
}

Object.defineProperty(globalThis, 'localStorage', { value: makeMemoryStorage(), writable: true, configurable: true });
Object.defineProperty(window, 'localStorage', { value: globalThis.localStorage, writable: true, configurable: true });

const { useChatStore } = await import('../store');
const { buildStreamChatRequestForSlot } = await import('../runtime');

const initialSettings = useChatStore.getState().settings;
const ANSWER = { approvalId: 'ap-1', decision: 'allow_once' as const };

function resetStore(queryMode: typeof initialSettings.queryMode = 'agent') {
  localStorage.clear();
  useChatStore.setState({ slots: {}, activeSlotId: null, settings: { ...initialSettings, queryMode } });
}

beforeEach(() => resetStore());
afterEach(() => resetStore());

describe('buildStreamChatRequestForSlot with a tool approval answer', () => {
  it('carries the answer', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    const request = buildStreamChatRequestForSlot(slotId, 'Allow once: create_issue on Jira', undefined, ANSWER);

    expect(request?.toolApproval).toEqual(ANSWER);
    expect(request?.chatMode).toBe('agent');
  });

  it('sends it in Agent mode even after the assistant chat switched modes', () => {
    resetStore('web-search');
    const slotId = useChatStore.getState().createSlot('conv-1');

    const answer = buildStreamChatRequestForSlot(slotId, 'Deny: create_issue on Jira', undefined, ANSWER);
    const plain = buildStreamChatRequestForSlot(slotId, 'hello');

    expect(answer?.chatMode).toBe('agent');
    expect(answer?.agentCapabilities).toEqual(useChatStore.getState().settings.agentCapabilities);
    expect(plain?.chatMode).toBe('web_search');
    expect(plain).not.toHaveProperty('toolApproval');
  });

  it('can be sent in Agent mode without an approval answer', () => {
    resetStore('web-search');
    const slotId = useChatStore.getState().createSlot('conv-1');

    const retry = buildStreamChatRequestForSlot(slotId, 'I signed in again.', undefined, undefined, { agentMode: true });

    expect(retry?.chatMode).toBe('agent');
    expect(retry).not.toHaveProperty('toolApproval');
  });

  it('goes to the agent in an agent chat', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-1' });

    const request = buildStreamChatRequestForSlot(slotId, 'Allow once: create_issue on Jira', undefined, ANSWER);

    expect(request?.agentId).toBe('agent-1');
    expect(request?.toolApproval).toEqual(ANSWER);
  });
});

describe('buildStreamChatRequestForSlot with an edited answer', () => {
  it('carries argsHash, arguments and feedback as given', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    const edited = { approvalId: 'ap-1', decision: 'allow' as const, argsHash: 'h', arguments: { a: 1 } };
    const denied = { approvalId: 'ap-1', decision: 'deny' as const, argsHash: 'h', feedback: 'no' };
    expect(buildStreamChatRequestForSlot(slotId, 'x', undefined, edited)?.toolApproval).toEqual(edited);
    expect(buildStreamChatRequestForSlot(slotId, 'x', undefined, denied)?.toolApproval).toEqual(denied);
  });
});

describe('validateApprovalEdits', () => {
  it('posts to the validate route and returns the errors', async () => {
    const { apiClient } = await import('@/lib/api');
    const { validateApprovalEdits } = await import('../api');
    const errors = [{ path: '/a', message: 'bad' }];
    vi.mocked(apiClient.post).mockResolvedValueOnce({ status: 200, data: { errors } });
    await expect(validateApprovalEdits('ap 1', 'h', { a: 1 })).resolves.toEqual(errors);
    expect(vi.mocked(apiClient.post).mock.calls.at(-1)?.slice(0, 2)).toEqual([
      '/api/v1/mcp-servers/tool-approvals/ap%201/validate',
      { argsHash: 'h', arguments: { a: 1 } },
    ]);
  });

  it('maps 404 and 410 to one whole-call error and throws on other failures', async () => {
    const { apiClient } = await import('@/lib/api');
    const { validateApprovalEdits } = await import('../api');
    vi.mocked(apiClient.post).mockResolvedValueOnce({ status: 410, data: { message: 'Expired' } });
    await expect(validateApprovalEdits('a', 'h', {})).resolves.toEqual([{ path: '', message: 'Expired' }]);
    vi.mocked(apiClient.post).mockResolvedValueOnce({ status: 404, data: {} });
    await expect(validateApprovalEdits('a', 'h', {})).resolves.toMatchObject([{ path: '' }]);
    vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('network'));
    await expect(validateApprovalEdits('a', 'h', {})).rejects.toThrow('network');
  });
});
