/**
 * Unit tests for the agent capabilities localStorage helpers and
 * AgentCapabilitiesBar component.
 */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { DEFAULT_AGENT_CAPABILITIES } from '../types';
import type { AgentCapabilities } from '../types';
import { useChatStore, ASSISTANT_CTX } from '../store';
import { applyConversationModelInfoToStore } from '../utils/apply-conversation-model-info';

// `apply-conversation-model-info.ts` fire-and-forgets a catalog refresh via
// `fetchModelsForContext`, which otherwise pulls in the real axios instance
// (and its localStorage-backed auth hydration) — irrelevant here since we
// only assert the synchronous queryMode/capabilities mapping. vi.mock calls
// are hoisted above these imports by Vitest regardless of file position.
vi.mock('@/chat/api', () => ({
  ChatApi: {
    fetchAvailableLlms: vi.fn().mockRejectedValue(new Error('not available in tests')),
    listCollectionsForChat: vi.fn(),
  },
}));
vi.mock('@/app/(main)/agents/api', () => ({
  AgentsApi: { getAgent: vi.fn().mockRejectedValue(new Error('not available in tests')) },
}));
// CollectionsTab pulls this in to decide whether to show the "all connectors by
// default" hint — it depends on Next.js router context (usePathname) that isn't
// mounted in these unit tests, so stub it out at the boundary.
vi.mock('@/chat/hooks/use-main-chat-connector-default-hint', () => ({
  useMainChatConnectorDefaultHint: () => false,
}));

afterEach(() => cleanup());

// ---------------------------------------------------------------------------
// Minimal in-memory localStorage stub
// ---------------------------------------------------------------------------

function makeLocalStorage(): Storage & { _data: Record<string, string> } {
  const _data: Record<string, string> = {};
  return {
    _data,
    getItem(key: string) { return Object.prototype.hasOwnProperty.call(_data, key) ? _data[key] : null; },
    setItem(key: string, value: string) { _data[key] = String(value); },
    removeItem(key: string) { delete _data[key]; },
    clear() { Object.keys(_data).forEach((k) => delete _data[k]); },
    get length() { return Object.keys(_data).length; },
    key(index: number) { return Object.keys(_data)[index] ?? null; },
  };
}

// ---------------------------------------------------------------------------
// Re-implement the localStorage helpers inline (mirrors store.ts logic)
// ---------------------------------------------------------------------------

const LS_KEY = 'pipeshub-agent-capabilities';

function makeHelpers(storage: Storage) {
  function lsGet(agentId?: string): AgentCapabilities {
    try {
      const key = agentId ? `${LS_KEY}:${agentId}` : LS_KEY;
      const raw = storage.getItem(key);
      if (!raw) return { ...DEFAULT_AGENT_CAPABILITIES };
      const parsed = JSON.parse(raw) as Partial<AgentCapabilities>;
      return {
        internalSearch: typeof parsed.internalSearch === 'boolean' ? parsed.internalSearch : true,
        webSearch: typeof parsed.webSearch === 'boolean' ? parsed.webSearch : true,
        deepSearch: typeof parsed.deepSearch === 'boolean' ? parsed.deepSearch : false,
      };
    } catch {
      return { ...DEFAULT_AGENT_CAPABILITIES };
    }
  }

  function lsSet(caps: AgentCapabilities, agentId?: string): void {
    const key = agentId ? `${LS_KEY}:${agentId}` : LS_KEY;
    storage.setItem(key, JSON.stringify(caps));
  }

  return { lsGet, lsSet };
}

// Use React.createElement so we don't need a separate JSX transform
const h = React.createElement;

// ---------------------------------------------------------------------------
// DEFAULT_AGENT_CAPABILITIES
// ---------------------------------------------------------------------------

describe('DEFAULT_AGENT_CAPABILITIES', () => {
  it('has both search modes enabled and deepSearch off', () => {
    expect(DEFAULT_AGENT_CAPABILITIES.internalSearch).toBe(true);
    expect(DEFAULT_AGENT_CAPABILITIES.webSearch).toBe(true);
    expect(DEFAULT_AGENT_CAPABILITIES.deepSearch).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// localStorage helpers
// ---------------------------------------------------------------------------

describe('localStorage agent capabilities helpers', () => {
  let storage: ReturnType<typeof makeLocalStorage>;
  let lsGet: ReturnType<typeof makeHelpers>['lsGet'];
  let lsSet: ReturnType<typeof makeHelpers>['lsSet'];

  beforeEach(() => {
    storage = makeLocalStorage();
    ({ lsGet, lsSet } = makeHelpers(storage));
  });

  it('returns defaults when nothing is stored', () => {
    expect(lsGet()).toEqual(DEFAULT_AGENT_CAPABILITIES);
  });

  it('returns defaults for an unknown agentId', () => {
    expect(lsGet('agent-xyz')).toEqual(DEFAULT_AGENT_CAPABILITIES);
  });

  it('round-trips universal capabilities', () => {
    const caps: AgentCapabilities = { internalSearch: false, webSearch: true, deepSearch: false };
    lsSet(caps);
    expect(lsGet()).toEqual(caps);
  });

  it('round-trips scoped capabilities per agentId', () => {
    const caps: AgentCapabilities = { internalSearch: true, webSearch: false, deepSearch: false };
    lsSet(caps, 'agent-42');
    expect(lsGet('agent-42')).toEqual(caps);
  });

  it('scoped and universal keys are independent', () => {
    const universal: AgentCapabilities = { internalSearch: false, webSearch: true, deepSearch: false };
    const scoped: AgentCapabilities = { internalSearch: true, webSearch: false, deepSearch: false };
    lsSet(universal);
    lsSet(scoped, 'agent-abc');
    expect(lsGet()).toEqual(universal);
    expect(lsGet('agent-abc')).toEqual(scoped);
  });

  it('falls back to defaults when stored JSON is corrupt', () => {
    storage.setItem(LS_KEY, 'not valid json {{{');
    expect(lsGet()).toEqual(DEFAULT_AGENT_CAPABILITIES);
  });

  it('uses boolean defaults for missing keys in stored object', () => {
    storage.setItem(LS_KEY, JSON.stringify({ internalSearch: false }));
    const caps = lsGet();
    expect(caps.internalSearch).toBe(false);
    expect(caps.webSearch).toBe(true);
    expect(caps.deepSearch).toBe(false);
  });

  it('ignores non-boolean values and returns defaults for those fields', () => {
    storage.setItem(LS_KEY, JSON.stringify({ internalSearch: 'yes', webSearch: 0 }));
    const caps = lsGet();
    expect(caps.internalSearch).toBe(true);
    expect(caps.webSearch).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AgentCapabilitiesBar component
// ---------------------------------------------------------------------------

describe('AgentCapabilitiesBar', () => {
  async function importBar() {
    const mod = await import(
      '../components/chat-panel/expansion-panels/agent-capabilities-bar'
    );
    return mod.AgentCapabilitiesBar;
  }

  type BarProps = Parameters<Awaited<ReturnType<typeof importBar>>>[0];

  function renderBar(props: BarProps, Bar: Awaited<ReturnType<typeof importBar>>) {
    return render(h(Theme, null, h(Bar, props)));
  }

  it('renders Capabilities label, Indexed Data Search and Web Search text (panel variant)', async () => {
    const Bar = await importBar();
    renderBar(
      {
        internalSearch: true,
        webSearch: true,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: vi.fn(),
      },
      Bar,
    );
    expect(screen.getByText('Capabilities')).toBeTruthy();
    expect(screen.getByText('Indexed Data Search')).toBeTruthy();
    expect(screen.getByText('Web Search')).toBeTruthy();
  });

  it('omits the Capabilities label and divider in toolbar variant', async () => {
    const Bar = await importBar();
    const { container } = renderBar(
      {
        internalSearch: true,
        webSearch: true,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: vi.fn(),
        variant: 'toolbar',
      },
      Bar,
    );
    expect(screen.queryByText('Capabilities')).toBeNull();
    expect(screen.getByText('Indexed Data Search')).toBeTruthy();
    expect(screen.getByText('Web Search')).toBeTruthy();
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.borderBottom).toBe('');
  });

  it('disables the internal search switch when agentHasInternalSearch=false', async () => {
    const Bar = await importBar();
    const { container } = renderBar(
      {
        internalSearch: false,
        webSearch: true,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: vi.fn(),
        agentHasInternalSearch: false,
      },
      Bar,
    );
    const [internalSwitch] = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    expect(internalSwitch.disabled).toBe(true);
  });

  it('disables the web search switch when agentHasWebSearch=false', async () => {
    const Bar = await importBar();
    const { container } = renderBar(
      {
        internalSearch: true,
        webSearch: false,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: vi.fn(),
        agentHasWebSearch: false,
      },
      Bar,
    );
    const switches = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    expect(switches[1].disabled).toBe(true);
  });

  it('calls onToggleInternalSearch when the enabled switch is clicked', async () => {
    const Bar = await importBar();
    const toggle = vi.fn();
    const { container } = renderBar(
      {
        internalSearch: true,
        webSearch: true,
        onToggleInternalSearch: toggle,
        onToggleWebSearch: vi.fn(),
      },
      Bar,
    );
    const [internalSwitch] = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    fireEvent.click(internalSwitch);
    expect(toggle).toHaveBeenCalled();
  });

  it('does NOT call onToggleInternalSearch when the capability is absent', async () => {
    const Bar = await importBar();
    const toggle = vi.fn();
    const { container } = renderBar(
      {
        internalSearch: false,
        webSearch: true,
        onToggleInternalSearch: toggle,
        onToggleWebSearch: vi.fn(),
        agentHasInternalSearch: false,
      },
      Bar,
    );
    const [internalSwitch] = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    fireEvent.click(internalSwitch);
    expect(toggle).not.toHaveBeenCalled();
  });

  it('calls onToggleWebSearch when the enabled switch is clicked', async () => {
    const Bar = await importBar();
    const toggle = vi.fn();
    const { container } = renderBar(
      {
        internalSearch: true,
        webSearch: true,
        onToggleInternalSearch: vi.fn(),
        onToggleWebSearch: toggle,
      },
      Bar,
    );
    const switches = container.querySelectorAll<HTMLButtonElement>('button[role="switch"]');
    fireEvent.click(switches[1]);
    expect(toggle).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Legacy chatMode -> Agent-mode capability mapping
// (apply-conversation-model-info.ts — reopening old conversations that used
// the now-removed standalone "Internal Search" / "Web Search" modes)
// ---------------------------------------------------------------------------

describe('applyConversationModelInfoToStore — legacy mode mapping', () => {
  const initialState = useChatStore.getState();

  beforeEach(() => {
    useChatStore.setState(initialState, true);
  });

  afterEach(() => {
    useChatStore.setState(initialState, true);
  });

  it('maps legacy "web_search" chatMode to Agent mode with webSearch on / internalSearch off', () => {
    applyConversationModelInfoToStore(
      { chatMode: 'web_search' } as never,
      ASSISTANT_CTX,
    );
    const { settings } = useChatStore.getState();
    expect(settings.queryMode).toBe('agent');
    expect(settings.agentCapabilities).toEqual({ internalSearch: false, webSearch: true, deepSearch: false });
  });

  it('maps legacy "web-search" chatMode (already-normalized form) the same way', () => {
    applyConversationModelInfoToStore(
      { chatMode: 'web-search' } as never,
      ASSISTANT_CTX,
    );
    const { settings } = useChatStore.getState();
    expect(settings.queryMode).toBe('agent');
    expect(settings.agentCapabilities).toEqual({ internalSearch: false, webSearch: true, deepSearch: false });
  });

  it('maps legacy "Internal Search" (unrecognized/default) chatMode to Agent mode with internalSearch on / webSearch off', () => {
    applyConversationModelInfoToStore(
      { chatMode: 'chat' } as never,
      ASSISTANT_CTX,
    );
    const { settings } = useChatStore.getState();
    expect(settings.queryMode).toBe('agent');
    expect(settings.agentCapabilities).toEqual({ internalSearch: true, webSearch: false, deepSearch: false });
  });

  it('leaves genuine Agent-mode conversations untouched (no capability override)', () => {
    applyConversationModelInfoToStore(
      { chatMode: 'agent' } as never,
      ASSISTANT_CTX,
    );
    const { settings } = useChatStore.getState();
    expect(settings.queryMode).toBe('agent');
    // Agent-mode restoration doesn't touch capabilities — defaults are preserved.
    expect(settings.agentCapabilities).toEqual(initialState.settings.agentCapabilities);
  });
});

// ---------------------------------------------------------------------------
// setAgentSidebarAgentId — scoped capability rehydration from localStorage
// ---------------------------------------------------------------------------

describe('setAgentSidebarAgentId — scoped capability rehydration', () => {
  const initialState = useChatStore.getState();
  const LS_CAPS_KEY = 'pipeshub-agent-capabilities';
  // The real global `localStorage` isn't reliably usable under this Vitest/Node
  // combo (jsdom's Storage vs. Node's own experimental webstorage global can
  // collide) — stub it per-test, same approach `makeLocalStorage()` uses above.
  let storage: ReturnType<typeof makeLocalStorage>;

  beforeEach(() => {
    useChatStore.setState(initialState, true);
    storage = makeLocalStorage();
    vi.stubGlobal('localStorage', storage);
  });

  afterEach(() => {
    useChatStore.setState(initialState, true);
    vi.unstubAllGlobals();
  });

  it('seeds scopedAgentCapabilities from localStorage the first time an agent is visited', () => {
    storage.setItem(
      `${LS_CAPS_KEY}:agent-99`,
      JSON.stringify({ internalSearch: false, webSearch: true, deepSearch: false }),
    );
    useChatStore.getState().setAgentSidebarAgentId('agent-99');
    expect(useChatStore.getState().scopedAgentCapabilities['agent-99']).toEqual({
      internalSearch: false,
      webSearch: true,
      deepSearch: false,
    });
  });

  it('falls back to DEFAULT_AGENT_CAPABILITIES when nothing is stored for that agent', () => {
    useChatStore.getState().setAgentSidebarAgentId('agent-fresh');
    expect(useChatStore.getState().scopedAgentCapabilities['agent-fresh']).toEqual(
      DEFAULT_AGENT_CAPABILITIES,
    );
  });

  it('does not clobber an already-loaded scoped entry when revisiting the same agent', () => {
    useChatStore.getState().setAgentSidebarAgentId('agent-1');
    useChatStore.getState().setScopedAgentCapabilities('agent-1', { internalSearch: false });
    // Navigate to a different agent, then back to agent-1 within the same session.
    useChatStore.getState().setAgentSidebarAgentId('agent-2');
    useChatStore.getState().setAgentSidebarAgentId('agent-1');
    expect(useChatStore.getState().scopedAgentCapabilities['agent-1'].internalSearch).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Selected-model-per-context localStorage persistence
// ---------------------------------------------------------------------------

describe('setSelectedModelForCtx — localStorage persistence', () => {
  const initialState = useChatStore.getState();
  const LS_MODELS_KEY = 'pipeshub-chat-selected-models';
  let storage: ReturnType<typeof makeLocalStorage>;

  beforeEach(() => {
    useChatStore.setState(initialState, true);
    storage = makeLocalStorage();
    vi.stubGlobal('localStorage', storage);
  });

  afterEach(() => {
    useChatStore.setState(initialState, true);
    vi.unstubAllGlobals();
  });

  it('writes the merged selected-models map to localStorage', () => {
    const model = { modelKey: 'gpt-4o', modelName: 'gpt-4o', modelFriendlyName: 'GPT-4o' };
    useChatStore.getState().setSelectedModelForCtx(ASSISTANT_CTX, model);

    expect(useChatStore.getState().settings.selectedModels[ASSISTANT_CTX]).toEqual(model);
    const stored = JSON.parse(storage.getItem(LS_MODELS_KEY) ?? '{}');
    expect(stored[ASSISTANT_CTX]).toEqual(model);
  });

  it('persists a null override (explicit "use default") for a context', () => {
    useChatStore.getState().setSelectedModelForCtx(ASSISTANT_CTX, null);
    const stored = JSON.parse(storage.getItem(LS_MODELS_KEY) ?? '{}');
    expect(stored[ASSISTANT_CTX]).toBeNull();
  });

  it('keeps separate contexts independent in the persisted map', () => {
    const modelA = { modelKey: 'a', modelName: 'a', modelFriendlyName: 'A' };
    const modelB = { modelKey: 'b', modelName: 'b', modelFriendlyName: 'B' };
    useChatStore.getState().setSelectedModelForCtx('ctx-a', modelA);
    useChatStore.getState().setSelectedModelForCtx('ctx-b', modelB);

    const stored = JSON.parse(storage.getItem(LS_MODELS_KEY) ?? '{}');
    expect(stored['ctx-a']).toEqual(modelA);
    expect(stored['ctx-b']).toEqual(modelB);
  });
});

// ---------------------------------------------------------------------------
// ResourceSectionHeader
// ---------------------------------------------------------------------------

describe('ResourceSectionHeader', () => {
  it('renders the label and an optional item count', async () => {
    const { ResourceSectionHeader } = await import(
      '../components/chat-panel/expansion-panels/resource-section-header'
    );
    render(h(Theme, null, h(ResourceSectionHeader, { label: 'Connectors', count: 3 })));
    expect(screen.getByText('Connectors')).toBeTruthy();
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('omits the count node when count is undefined', async () => {
    const { ResourceSectionHeader } = await import(
      '../components/chat-panel/expansion-panels/resource-section-header'
    );
    render(h(Theme, null, h(ResourceSectionHeader, { label: 'Actions' })));
    expect(screen.getByText('Actions')).toBeTruthy();
    expect(screen.queryByText('0')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// CollectionsTab — filterMode + controlledSearchQuery (per-tab Connectors /
// Collections panels)
// ---------------------------------------------------------------------------

describe('CollectionsTab — filterMode with controlled search', () => {
  async function importTab() {
    const mod = await import(
      '../components/chat-panel/expansion-panels/connectors-collections/collections-tab'
    );
    return mod.CollectionsTab;
  }

  beforeEach(async () => {
    const { ChatApi } = await import('../api');
    vi.mocked(ChatApi.listCollectionsForChat).mockResolvedValue({
      knowledgeBases: [
        {
          id: 'conn-1',
          name: 'Google Drive',
          nodeType: 'CONNECTOR',
          origin: 'CONNECTOR',
          connector: 'GOOGLE_DRIVE',
          createdAtTimestamp: 1,
          updatedAtTimestamp: 2,
          createdBy: '',
          userRole: '',
          folders: [],
        },
        {
          id: 'coll-1',
          name: "Abhishek's private KB",
          nodeType: 'KB',
          origin: 'COLLECTION',
          createdAtTimestamp: 1,
          updatedAtTimestamp: 2,
          createdBy: '',
          userRole: '',
          folders: [],
        },
      ],
      requestedPage: 1,
      requestedLimit: 20,
      serverPagination: undefined,
    } as never);
  });

  it('filterMode="connectors" shows only connector rows, not collections', async () => {
    const CollectionsTab = await importTab();
    render(
      h(
        Theme,
        null,
        h(CollectionsTab, {
          apps: [],
          kb: [],
          onSelectionChange: vi.fn(),
          filterMode: 'connectors',
          controlledSearchQuery: '',
        }),
      ),
    );
    await screen.findByText('Google Drive');
    expect(screen.queryByText("Abhishek's private KB")).toBeNull();
  });

  it('filterMode="collections" shows only collection rows, not connectors', async () => {
    const CollectionsTab = await importTab();
    render(
      h(
        Theme,
        null,
        h(CollectionsTab, {
          apps: [],
          kb: [],
          onSelectionChange: vi.fn(),
          filterMode: 'collections',
          controlledSearchQuery: '',
        }),
      ),
    );
    await screen.findByText("Abhishek's private KB");
    expect(screen.queryByText('Google Drive')).toBeNull();
  });

  it('hides its own search input when controlledSearchQuery is provided', async () => {
    const CollectionsTab = await importTab();
    const { container } = render(
      h(
        Theme,
        null,
        h(CollectionsTab, {
          apps: [],
          kb: [],
          onSelectionChange: vi.fn(),
          filterMode: 'connectors',
          controlledSearchQuery: '',
        }),
      ),
    );
    await screen.findByText('Google Drive');
    expect(container.querySelector('input.collections-search-input')).toBeNull();
  });

  it('filters rows by the externally-controlled search query', async () => {
    const CollectionsTab = await importTab();
    render(
      h(
        Theme,
        null,
        h(CollectionsTab, {
          apps: [],
          kb: [],
          onSelectionChange: vi.fn(),
          filterMode: 'connectors',
          controlledSearchQuery: 'drive',
        }),
      ),
    );
    await screen.findByText('Google Drive');
    expect(screen.queryByText("Abhishek's private KB")).toBeNull();
  });
});
