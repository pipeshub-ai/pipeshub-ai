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
