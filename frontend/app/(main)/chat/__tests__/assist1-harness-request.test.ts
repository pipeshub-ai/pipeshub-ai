/**
 * ASSIST1 AS1-13 through the composer's request builder and the store: the
 * switch is remembered per viewer, and a new Agent-mode turn carries
 * `harness: "durable"` only when the org offers it and the switch is on.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';

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


const { useChatStore, lsGetNewHarness } = await import('../store');
const { buildStreamChatRequestForSlot } = await import('../runtime');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');

const initialSettings = useChatStore.getState().settings;
const ON = { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true };
const OFF = { useHarness: false, harnessMajor: 2, reason: 'org_disabled', resumeInFlightRuns: true, harnessOn: false };

function world({ harnessOn, queryMode = 'agent' }: { harnessOn: boolean; queryMode?: 'agent' | 'chat' }) {
  localStorage.clear();
  useChatStore.setState({ slots: {}, activeSlotId: null, agentNeedsHarness: false, settings: { ...initialSettings, queryMode, newHarness: true } });
  // An older server still sends the retired Labs switch: it is not read (CTL-5).
  useFeatureFlagsStore.setState({ flags: { ENABLE_DURABLE_ASSISTANT_AGENT: false }, agentHarness: harnessOn ? ON : OFF });
}

describe('CTL-5: the default chat\'s Agent mode comes with the agent harness, with no switch of its own', () => {
  it.each([
    ['harness on: a new Agent-mode conversation asks for it', { harnessOn: true }, 'durable'],
    ['harness off', { harnessOn: false }, undefined],
    ['another mode', { harnessOn: true, queryMode: 'chat' as const }, undefined],
  ])('%s', (_name, setup, expected) => {
    world(setup);
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.harness).toBe(expected);
  });

  it('the store keeps no per-viewer switch for it', () => {
    expect(useChatStore.getState().settings).not.toHaveProperty('durableAssistantAgent');
    expect(useChatStore.getState()).not.toHaveProperty('setDurableAssistantAgent');
  });
});

describe('CTL-7: the "New agent harness" switch decides how a new conversation starts', () => {
  it('is on by default, remembers off in this browser, and reads on when storage fails', () => {
    localStorage.clear();
    expect(lsGetNewHarness()).toBe(true);
    useChatStore.getState().setNewHarness(false);
    expect(localStorage.getItem('pipeshub-new-agent-harness')).toBe('false');
    expect(lsGetNewHarness()).toBe(false);
    useChatStore.getState().setNewHarness(true);
    expect(lsGetNewHarness()).toBe(true);
    const failing = vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(lsGetNewHarness()).toBe(true);
    failing.mockRestore();
  });

  it.each([
    ['switch off, Agent mode: classic', { queryMode: 'agent' as const }, false, 'legacy'],
    ['switch off, search mode: classic', { queryMode: 'chat' as const }, false, 'legacy'],
    ['switch on: today\'s request', { queryMode: 'agent' as const }, true, 'durable'],
  ])('%s', (_name, setup, chosen, expected) => {
    world({ harnessOn: true, ...setup });
    useChatStore.getState().setNewHarness(chosen);
    const slotId = useChatStore.getState().createSlot(null);
    expect(buildStreamChatRequestForSlot(slotId, 'hello')?.harness).toBe(expected);
  });

  it('the org\'s harness off: the switch is not read', () => {
    world({ harnessOn: false });
    useChatStore.getState().setNewHarness(false);
    const slotId = useChatStore.getState().createSlot(null);
    expect(buildStreamChatRequestForSlot(slotId, 'hello')?.harness).toBeUndefined();
  });

  it('an agent chat with the switch off asks for the classic path', () => {
    world({ harnessOn: true });
    useChatStore.getState().setNewHarness(false);
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-1' });
    expect(buildStreamChatRequestForSlot(slotId, 'hello')).toMatchObject({ agentId: 'agent-1', harness: 'legacy' });
  });

  it('an agent that needs the harness never asks for the classic path', () => {
    world({ harnessOn: true });
    useChatStore.getState().setNewHarness(false);
    useChatStore.setState({ agentNeedsHarness: true });
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-1' });
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.agentId).toBe('agent-1');
    expect(request?.harness).toBeUndefined();
  });
});

describe('R31JOURNEY-FU-7: the options matrix routes the default chat as this request builder asks', () => {
  // The e2e oracle (scripts/routing_table.py) reads its routing input from the shared routing table. Held here to what
  // the web app sends, so the matrix cannot model a request the product no longer makes (it routed Agent mode legacy
  // from ASSIST1 until R31-JFIX, so no engine job of the default chat could be a row).
  const table = JSON.parse(
    readFileSync(path.resolve(__dirname, '../../../../../contracts/harness/vectors/routing.json'), 'utf8'),
  ) as { e2e: { modes: Record<string, { kind: string; chatMode: string; harnessRequest?: string }> } };
  const modes = table.e2e.modes;

  it.each([
    ['universal', 'agent'],
    ['internal_search', 'chat'],
    ['plain', 'chat'],
  ] as const)('%s is a new default-chat conversation in the web app\'s %s mode, the harness on and the switch at its default', (mode, queryMode) => {
    world({ harnessOn: true, queryMode });
    const request = buildStreamChatRequestForSlot(useChatStore.getState().createSlot(null), 'hello');
    expect(modes[mode].kind).toBe('assistant');
    expect(request?.agentId).toBeUndefined();
    expect(request?.chatMode).toBe(modes[mode].chatMode);
    expect(request?.harness ?? 'none').toBe(modes[mode].harnessRequest ?? 'none');
  });
});
