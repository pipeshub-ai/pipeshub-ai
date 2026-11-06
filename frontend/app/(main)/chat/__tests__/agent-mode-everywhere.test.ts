/**
 * Agent mode is the only mode anyone can choose. What an older chat was stored with never takes the composer out of
 * it: not for that chat's next message, and not for the next new chat.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

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

vi.mock('@/chat/utils/fetch-models-for-context', () => ({
  fetchModelsForContext: vi.fn().mockResolvedValue([]),
  OrgModelsFetchError: class extends Error {},
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

const { useChatStore, ASSISTANT_CTX, ctxKeyFromAgent } = await import('../store');
const { buildStreamChatRequestForSlot } = await import('../runtime');
const { buildStreamRequestModeFields } = await import('../types');
const { applyConversationModelInfoToStore } = await import('../utils/apply-conversation-model-info');

const initialSettings = useChatStore.getState().settings;
const settings = () => useChatStore.getState().settings;
const open = (chatMode: string | undefined, ctxKey: string = ASSISTANT_CTX) =>
  applyConversationModelInfoToStore({ modelKey: 'k', modelName: 'm', chatMode }, ctxKey);

beforeEach(() => {
  localStorage.clear();
  useChatStore.setState({ slots: {}, activeSlotId: null, settings: { ...initialSettings } });
});

describe('opening a chat', () => {
  const STORED = ['internal_search', 'web_search', 'image', 'chat', 'quick', 'auto', 'deep', 'verification', 'agent', 'agent:quick', ''];

  it.each(STORED)('stored as "%s" leaves the composer in agent mode', (chatMode) => {
    open(chatMode);
    expect(settings().queryMode).toBe('agent');
  });

  it('brings back agent mode if something else had left it', () => {
    useChatStore.setState((s) => ({ settings: { ...s.settings, queryMode: 'chat' } }));
    open('quick');
    expect(settings().queryMode).toBe('agent');
  });

  it('with no mode stored, brings agent mode back too', () => {
    useChatStore.setState((s) => ({ settings: { ...s.settings, queryMode: 'chat' } }));
    open(undefined);
    expect(settings().queryMode).toBe('agent');
  });

  it('never carries a web search scope over: agent mode keeps the selection', () => {
    useChatStore.setState((s) => ({ settings: { ...s.settings, filters: { apps: ['app-1'], kb: [] } } }));
    open('web_search');
    expect(settings().filters.apps).toEqual(['app-1']);
  });

  it("keeps an agent chat's strategy from what it stored", () => {
    open('deep', ctxKeyFromAgent('agent-1'));
    expect(settings().queryMode).toBe('agent');
    expect(settings().agentStrategy).toBe('deep');
  });
});

describe('after opening a chat stored in an older mode', () => {
  it.each(['internal_search', 'web_search', 'image'])('from "%s", the next new chat starts in agent mode', (chatMode) => {
    open(chatMode);
    const fresh = useChatStore.getState().createSlot(null);
    expect(buildStreamChatRequestForSlot(fresh, 'hello')?.chatMode).toBe('agent');
  });

  it('the loop is gone: chats made from one another stay in agent mode, however many in a row', () => {
    for (let chat = 0; chat < 3; chat += 1) {
      open('internal_search');
      const fresh = useChatStore.getState().createSlot(null);
      const stored = buildStreamChatRequestForSlot(fresh, 'hello')?.chatMode;
      expect(stored).toBe('agent');
      open(stored);
    }
  });

  it.each(['internal_search', 'quick'])('from "%s", that chat sends and regenerates as agent', (chatMode) => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    open(chatMode);
    expect(buildStreamChatRequestForSlot(slotId, 'and now?')?.chatMode).toBe('agent');
    expect(buildStreamRequestModeFields(settings()).chatMode).toBe('agent');
  });
});
