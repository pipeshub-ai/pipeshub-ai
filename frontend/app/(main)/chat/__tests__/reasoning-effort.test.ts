/**
 * Unit tests for reasoning-effort support:
 *  - `useChatStore`'s per-context `reasoningEffort` slice, kept for the
 *    session only, and the picker that sets and clears it.
 *  - `buildStreamChatRequestForSlot()` and `streamRegenerateForSlot()`
 *    forwarding the resolved reasoning effort, or omitting the field so the
 *    backend applies the model's default.
 *  - The label the chat shows when no effort is picked.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createElement } from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

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

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({
    t: (_key: string, opts?: string | { defaultValue: string; level?: string }) =>
      typeof opts === 'string' ? opts : (opts?.defaultValue ?? _key).replace('{{level}}', opts?.level ?? ''),
  }),
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
// warning). Replace it with a real in-memory `Storage` so the tests can plant
// a value an earlier version saved and check it is ignored.
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
const { streamRegenerateForSlot } = await import('../streaming');
const { ChatApi } = await import('../api');
const { normalizeReasoningEffort } = await import('../types');
const { applyConversationModelInfoToStore } = await import('../utils/apply-conversation-model-info');
const { getModelDefaultReasoningEffort } = await import('../store');
const { getAppliedReasoningEffortLabel, ReasoningEffortSelector } = await import(
  '../components/chat-panel/expansion-panels/model-selector/model-selector-panel'
);

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
      agentDefaultReasoningEffort: {},
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

  it('setReasoningEffortForCtx keeps the pick in memory only', () => {
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'medium');
    useChatStore.getState().setReasoningEffortForCtx('agent-42', 'medium');
    expect(localStorage.length).toBe(0);
  });

  it('setReasoningEffortForCtx(null) clears the pick', () => {
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'low');
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, null);
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBeNull();
  });

  it('scoped (per-agent) and universal contexts are independent', () => {
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'none');
    useChatStore.getState().setReasoningEffortForCtx('agent-abc', 'high');
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBe('none');
    expect(useChatStore.getState().settings.reasoningEffort['agent-abc']).toBe('high');
  });
});

describe('normalizeReasoningEffort', () => {
  it('floors "none" to "low"', () => {
    expect(normalizeReasoningEffort('none')).toBe('low');
  });

  it('passes through every other value (including null) unchanged', () => {
    expect(normalizeReasoningEffort('low')).toBe('low');
    expect(normalizeReasoningEffort('medium')).toBe('medium');
    expect(normalizeReasoningEffort('high')).toBe('high');
    expect(normalizeReasoningEffort('max')).toBe('max');
    expect(normalizeReasoningEffort(null)).toBeNull();
  });
});

describe('applyConversationModelInfoToStore — reasoningEffort', () => {
  // A conversation keeps the last effort sent and a turn sent without one
  // never clears it, so restoring it would bring an old "high" back for good.
  it("does not restore the effort the conversation recorded", () => {
    applyConversationModelInfoToStore(
      { modelKey: 'openai', modelName: 'gpt-5', chatMode: 'quick', reasoningEffort: 'high' },
      ASSISTANT_CTX,
    );
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX] ?? null).toBeNull();
  });

  it("keeps the effort picked in this session", () => {
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'medium');
    applyConversationModelInfoToStore(
      { modelKey: 'openai', modelName: 'gpt-5', chatMode: 'quick', reasoningEffort: 'low' },
      ASSISTANT_CTX,
    );
    expect(useChatStore.getState().settings.reasoningEffort[ASSISTANT_CTX]).toBe('medium');
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

function setAgentDefault(ctxKey: string, effort: import('../types').ReasoningEffort | null) {
  useChatStore.setState((state) => ({
    settings: {
      ...state.settings,
      agentDefaultReasoningEffort: { ...state.settings.agentDefaultReasoningEffort, [ctxKey]: effort },
    },
  }));
}

describe('buildStreamChatRequestForSlot — reasoningEffort forwarding', () => {
  it('omits reasoningEffort entirely when no model is configured for the context', () => {
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toBeNull();
    expect(request).not.toHaveProperty('reasoningEffort');
  });

  it('does not send an effort an earlier version saved in the browser', () => {
    localStorage.setItem(`pipeshub-reasoning-effort:${ASSISTANT_CTX}`, 'high');
    registerModel(ASSISTANT_CTX, { isReasoning: true });
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toBeNull();
    expect(request).not.toHaveProperty('reasoningEffort');
  });

  it('sends no effort after the pick is cleared', () => {
    registerModel(ASSISTANT_CTX, { isReasoning: true });
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'max');
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, null);
    const slotId = useChatStore.getState().createSlot(null);
    expect(buildStreamChatRequestForSlot(slotId, 'hello')).not.toHaveProperty('reasoningEffort');
  });

  // Sending "high" here would override the model's own default on the backend.
  it('omits reasoningEffort for a reasoning-capable model when neither the user nor the agent chose one', () => {
    registerModel(ASSISTANT_CTX, { isReasoning: true });
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toBeNull();
    expect(request).not.toHaveProperty('reasoningEffort');
  });

  it('omits reasoningEffort in an agent chat when the agent has no default and the user chose none', () => {
    registerModel('agent-99', { isReasoning: true });
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-99' });
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toBeNull();
    expect(request).not.toHaveProperty('reasoningEffort');
  });

  it("sends the agent's default effort when the user chose none", () => {
    registerModel('agent-99', { isReasoning: true });
    setAgentDefault('agent-99', 'medium');
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-99' });
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.reasoningEffort).toBe('medium');
  });

  it("the user's choice wins over the agent's default", () => {
    registerModel('agent-99', { isReasoning: true });
    setAgentDefault('agent-99', 'medium');
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-99' });
    useChatStore.getState().setReasoningEffortForCtx('agent-99', 'max');
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.reasoningEffort).toBe('max');
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

describe('streamRegenerateForSlot — reasoningEffort forwarding', () => {
  const model = { modelKey: 'openai', modelName: 'gpt-5', modelFriendlyName: 'GPT-5' };

  function regenSlot(agentId: string | null): string {
    const slotId = useChatStore.getState().createSlot('conv-1');
    if (agentId) useChatStore.getState().updateSlot(slotId, { threadAgentId: agentId });
    return slotId;
  }

  async function regenerate(agentId: string | null, answers?: string): Promise<Record<string, unknown>> {
    const assistant = vi.spyOn(ChatApi, 'streamRegenerate').mockResolvedValue(undefined);
    const agent = vi.spyOn(ChatApi, 'streamAgentRegenerate').mockResolvedValue(undefined);
    try {
      await streamRegenerateForSlot(regenSlot(agentId), 'a1', model, undefined, undefined, answers);
      if (agentId) {
        expect(assistant).not.toHaveBeenCalled();
        return agent.mock.calls[0][4] as Record<string, unknown>;
      }
      expect(agent).not.toHaveBeenCalled();
      return assistant.mock.calls[0][3] as Record<string, unknown>;
    } finally {
      assistant.mockRestore();
      agent.mockRestore();
    }
  }

  it('omits reasoningEffort for a reasoning-capable model when the user chose none', async () => {
    registerModel(ASSISTANT_CTX, { isReasoning: true });
    expect(await regenerate(null)).not.toHaveProperty('reasoningEffort');
  });

  const ANSWERS = 'User selections:\n1. "Which region?" → US';

  it('sends changed question-card answers, for an assistant chat and for an agent', async () => {
    registerModel(ASSISTANT_CTX, { isReasoning: false });
    expect(await regenerate(null, ANSWERS)).toMatchObject({ answers: ANSWERS });
    registerModel(ctxKeyFromAgent('agent-1'), { isReasoning: false });
    expect(await regenerate('agent-1', ANSWERS)).toMatchObject({ answers: ANSWERS });
  });

  // Python turns web search off when `tools` is a list without a web_search entry; omitted, it keeps every tool.
  it('leaves the tool list out when every tool is selected, as a normal send does', async () => {
    registerModel(ASSISTANT_CTX, { isReasoning: false });
    useChatStore.setState({
      universalAgentStreamTools: null,
      universalAgentToolCatalogFullNames: ['jira.search_issues', 'slack.send_message'],
    });
    expect(await regenerate(null)).not.toHaveProperty('agentStreamTools');
  });

  it('sends the chosen tools when only some are selected', async () => {
    registerModel(ASSISTANT_CTX, { isReasoning: false });
    useChatStore.setState({
      universalAgentStreamTools: ['inst-1:jira.search_issues'],
      universalAgentToolCatalogFullNames: ['inst-1:jira.search_issues', 'inst-1:slack.send_message'],
    });
    expect((await regenerate(null)).agentStreamTools).toEqual(['jira.search_issues']);
  });

  it('leaves answers out of an ordinary regenerate', async () => {
    registerModel(ASSISTANT_CTX, { isReasoning: false });
    expect(await regenerate(null)).not.toHaveProperty('answers');
  });

  it("sends the user's choice", async () => {
    registerModel(ASSISTANT_CTX, { isReasoning: true });
    useChatStore.getState().setReasoningEffortForCtx(ASSISTANT_CTX, 'low');
    expect((await regenerate(null)).reasoningEffort).toBe('low');
  });

  it('omits reasoningEffort in an agent chat when neither the user nor the agent chose one', async () => {
    registerModel('agent-99', { isReasoning: true });
    expect(await regenerate('agent-99')).not.toHaveProperty('reasoningEffort');
  });

  it("sends the agent's default effort when the user chose none", async () => {
    registerModel('agent-99', { isReasoning: true });
    setAgentDefault('agent-99', 'medium');
    expect((await regenerate('agent-99')).reasoningEffort).toBe('medium');
  });

  it("the user's choice wins over the agent's default", async () => {
    registerModel('agent-99', { isReasoning: true });
    setAgentDefault('agent-99', 'medium');
    useChatStore.getState().setReasoningEffortForCtx('agent-99', 'max');
    expect((await regenerate('agent-99')).reasoningEffort).toBe('max');
  });
});

describe('label for the effort a chat runs with', () => {
  const t = ((_key: string, opts: string | { defaultValue: string; level: string }) =>
    typeof opts === 'string' ? opts : opts.defaultValue.replace('{{level}}', opts.level)) as never;
  const qwen = { modelKey: 'k-qwen', modelName: 'qwen', modelFriendlyName: 'Qwen' };

  function setOrgModels(defaultReasoningEffort?: 'none' | 'low' | 'medium') {
    useChatStore.getState().setAvailableModelsForCtx(ASSISTANT_CTX, [
      {
        modelType: 'llm',
        provider: 'openAICompatible',
        modelName: 'qwen',
        modelKey: 'k-qwen',
        isMultimodal: false,
        isReasoning: true,
        isDefault: true,
        modelFriendlyName: 'Qwen',
        ...(defaultReasoningEffort && { defaultReasoningEffort }),
      },
    ]);
  }

  it("shows the model's own default when nothing is picked", () => {
    setOrgModels('low');
    const modelDefault = getModelDefaultReasoningEffort(ASSISTANT_CTX, qwen);

    expect(modelDefault).toBe('low');
    expect(getAppliedReasoningEffortLabel(t, { picked: null, modelDefault })).toBe('Default (Low)');
  });

  it('keeps showing High when the model stores no default', () => {
    setOrgModels();
    const modelDefault = getModelDefaultReasoningEffort(ASSISTANT_CTX, qwen);

    expect(modelDefault).toBeNull();
    expect(getAppliedReasoningEffortLabel(t, { picked: null, modelDefault })).toBe('High');
  });

  it("shows a pick or the agent's default over the model's default", () => {
    expect(getAppliedReasoningEffortLabel(t, { picked: 'max', modelDefault: 'low' })).toBe('Max');
    expect(
      getAppliedReasoningEffortLabel(t, { picked: null, agentDefault: 'medium', modelDefault: 'low' }),
    ).toBe('Medium');
  });

  it("shows a legacy 'none' default as the low effort it runs with", () => {
    setOrgModels('none');

    expect(getModelDefaultReasoningEffort(ASSISTANT_CTX, qwen)).toBe('low');
  });
});

describe('reasoning effort picker', () => {
  afterEach(() => cleanup());

  function renderPicker(props: {
    value: import('../types').ReasoningEffort | null;
    agentDefault?: import('../types').ReasoningEffort | null;
    modelDefault?: import('../types').ReasoningEffort | null;
  }) {
    const onSelect = vi.fn();
    render(
      createElement(ReasoningEffortSelector, {
        agentDefault: null,
        modelDefault: null,
        ...props,
        onSelect,
      }),
    );
    return onSelect;
  }

  it("marks the default level when nothing is picked and says it is the model's own", () => {
    renderPicker({ value: null, modelDefault: 'medium' });
    expect(screen.getByRole('radio', { name: 'Medium' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText("Defaults to Medium, this model's own default, when not set.")).toBeTruthy();
  });

  it('marks the pick over the default and says how to clear it', () => {
    const onSelect = renderPicker({ value: 'high', modelDefault: 'medium' });
    expect(screen.getByRole('radio', { name: 'High' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('radio', { name: 'Medium' }).getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText('Click again to use the default.')).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'High' }));
    expect(onSelect).toHaveBeenCalledWith('high');
  });

  it("marks the agent's default over the model's", () => {
    renderPicker({ value: null, agentDefault: 'low', modelDefault: 'max' });
    expect(screen.getByRole('radio', { name: 'Low' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Defaults to Low when not set.')).toBeTruthy();
  });
});
