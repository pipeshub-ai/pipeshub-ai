/**
 * Unit tests for reasoning-effort support:
 *  - `useChatStore`'s per-context localStorage-backed `reasoningEffort` slice
 *    (`setReasoningEffortForCtx` / `hydrateReasoningEffortForCtx`).
 *  - `buildStreamChatRequestForSlot()` forwarding the resolved reasoning
 *    effort (or omitting the field) onto the outgoing `StreamChatRequest`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// `runtime.ts` transitively pulls in several API modules that ultimately
// import `lib/store/auth-store.ts`, which eagerly hydrates itself from
// `window.localStorage` at module-load time — incompatible with this
// test's jsdom environment. None of that networking/auth code is
// exercised by `buildStreamChatRequestForSlot` (it's a pure request
// builder reading only from the zustand chat store), so stub it out.
vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: { getState: () => ({ isHydrated: true }) },
  hydrateAuthStore: vi.fn(),
  LOGIN_NAVIGATION_EVENT: 'pipeshub:request-login-navigation',
}));

vi.mock('@/lib/api', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    request: vi.fn(),
  },
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

// Node 22+'s built-in global `localStorage` shadows jsdom's implementation
// under Vitest and is a non-functional stub without `--localstorage-file`
// (see the `--localstorage-file was provided without a valid path`
// warning). Replace it with a real in-memory `Storage` so the store's
// localStorage-backed persistence can be exercised directly.
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

Object.defineProperty(globalThis, 'localStorage', {
  value: makeMemoryStorage(),
  writable: true,
  configurable: true,
});
Object.defineProperty(window, 'localStorage', {
  value: globalThis.localStorage,
  writable: true,
  configurable: true,
});

const { useChatStore, ctxKeyFromAgent, ASSISTANT_CTX } = await import('../store');
const { buildStreamChatRequestForSlot } = await import('../runtime');

const initialSettings = useChatStore.getState().settings;

function resetStore() {
  localStorage.clear();
  useChatStore.setState({
    slots: {},
    activeSlotId: null,
    settings: {
      ...initialSettings,
      selectedModels: {},
      defaultModels: {},
      reasoningEffort: {},
      availableModels: {},
    },
  });
}

beforeEach(() => resetStore());
afterEach(() => resetStore());

describe('reasoningEffort store actions', () => {
  it('starts with no reasoning effort recorded for a context', () => {
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBeUndefined();
  });

  it('setReasoningEffortForCtx updates in-memory state', () => {
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'high');
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBe('high');
  });

  it('setReasoningEffortForCtx persists to localStorage, scoped by ctxKey', () => {
    useChatStore.getState().setReasoningEffortForCtx('agent-42', 'medium');
    expect(localStorage.getItem('pipeshub-reasoning-effort:agent-42')).toBe('medium');
    expect(localStorage.getItem(`pipeshub-reasoning-effort:${ASSISTANT_CTX}`)).toBeNull();
  });

  it('setReasoningEffortForCtx(null) clears the persisted value', () => {
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'low');
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, null);
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBeNull();
    expect(localStorage.getItem(`pipeshub-reasoning-effort:${ASSISTANT_CTX}`)).toBeNull();
  });

  it('hydrateReasoningEffortForCtx loads a previously persisted value', () => {
    localStorage.setItem('pipeshub-reasoning-effort:agent-7', 'max');
    useChatStore.getState().hydrateReasoningEffortForCtx('agent-7');
    expect(useChatStore.getState().settings.reasoningEffort['agent-7']).toBe('max');
  });

  it('hydrateReasoningEffortForCtx resolves to null when nothing is persisted', () => {
    useChatStore.getState().hydrateReasoningEffortForCtx('agent-unknown');
    expect(useChatStore.getState().settings.reasoningEffort['agent-unknown']).toBeNull();
  });

  it('hydrateReasoningEffortForCtx ignores a corrupt/invalid stored value', () => {
    localStorage.setItem(`pipeshub-reasoning-effort:${ASSISTANT_CTX}`, 'ultra-high');
    useChatStore.getState().hydrateReasoningEffortForCtx(ASSISTANT_CTX);
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBeNull();
  });

  it('hydrateReasoningEffortForCtx does not overwrite an already-hydrated/explicit value', () => {
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'low');
    localStorage.setItem(`pipeshub-reasoning-effort:${ASSISTANT_CTX}`, 'high');
    useChatStore.getState().hydrateReasoningEffortForCtx(ASSISTANT_CTX);
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBe('low');
  });

  it('scoped (per-agent) and universal contexts are independent', () => {
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'none');
    useChatStore.getState().setReasoningEffortForCtx('agent-abc', 'high');
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBe('none');
    expect(useChatStore.getState().settings.reasoningEffort['agent-abc']).toBe('high');
  });
});

describe('ctxKeyFromAgent', () => {
  it('returns the universal ctx key when no agent id is given', () => {
    expect(ctxKeyFromAgent(null)).toBe(ASSISTANT_CTX);
    expect(ctxKeyFromAgent(undefined)).toBe(ASSISTANT_CTX);
    expect(ctxKeyFromAgent('')).toBe(ASSISTANT_CTX);
  });

  it('returns the agent id verbatim when provided', () => {
    expect(ctxKeyFromAgent('agent-42')).toBe('agent-42');
  });
});

/** Registers a model in the ctx's available-models catalog, as `fetchModelsForContext` would. */
function registerModel(
  ctxKey: string,
  overrides: Partial<import('../types').AvailableLlmModel> = {}
) {
  const model: import('../types').AvailableLlmModel = {
    modelType: 'llm',
    provider: 'openai',
    modelName: 'gpt-5',
    modelKey: 'openai',
    isMultimodal: false,
    isReasoning: true,
    isDefault: true,
    modelFriendlyName: 'GPT-5',
    ...overrides,
  };
  useChatStore.getState().setAvailableModelsForCtx(ctxKey, [model]);
  useChatStore.setState((state) => ({
    settings: {
      ...state.settings,
      defaultModels: {
        ...state.settings.defaultModels,
        [ctxKey]: {
          modelKey: model.modelKey,
          modelName: model.modelName,
          modelFriendlyName: model.modelFriendlyName,
        },
      },
    },
  }));
  return model;
}

describe('buildStreamChatRequestForSlot — reasoningEffort forwarding', () => {
  it('omits reasoningEffort entirely when no model is configured for the context', () => {
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toBeNull();
    expect(request).not.toHaveProperty('reasoningEffort');
  });

  it('defaults to DEFAULT_REASONING_EFFORT when the active model is reasoning-capable and no override is set', () => {
    registerModel(ASSISTANT_CTX, { isReasoning: true });
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.reasoningEffort).toBe('high');
  });

  it('omits reasoningEffort when the active model does not support reasoning', () => {
    registerModel(ASSISTANT_CTX, { isReasoning: false });
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toHaveProperty('reasoningEffort');
  });

  it('omits reasoningEffort when explicitly cleared to null and the model is not reasoning-capable', () => {
    registerModel(ASSISTANT_CTX, { isReasoning: false });
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toHaveProperty('reasoningEffort');
  });

  it('an explicit override still wins over the reasoning-capable default', () => {
    registerModel(ASSISTANT_CTX, { isReasoning: true });
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'low');
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.reasoningEffort).toBe('low');
  });

  it('forwards the selected reasoningEffort for the universal (assistant) context', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'high');
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.reasoningEffort).toBe('high');
  });

  it("resolves reasoningEffort per-agent using the slot's threadAgentId", () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-99' });
    useChatStore.getState().setReasoningEffortForCtx('agent-99', 'low');
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'max');

    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.reasoningEffort).toBe('low');
  });

  it('returns null for an unknown slot id', () => {
    const request = buildStreamChatRequestForSlot('does-not-exist', 'hello');
    expect(request).toBeNull();
  });
});
