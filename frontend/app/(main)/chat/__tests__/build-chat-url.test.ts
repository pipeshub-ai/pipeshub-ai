/**
 * Unit tests for `buildChatHref` (pure URL builder) and the
 * `openFreshAgentChat` / `openFreshProjectChat` navigation helpers.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// `build-chat-url.ts` imports `useChatStore`, which transitively pulls in
// API modules that eagerly hydrate `lib/store/auth-store.ts` from
// `window.localStorage` — incompatible with this test's jsdom environment
// unless stubbed (same setup as `reasoning-effort.test.ts`).
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

const { buildChatHref, openFreshAgentChat, openFreshProjectChat } = await import('../build-chat-url');
const { useChatStore } = await import('../store');

describe('buildChatHref', () => {
  it('returns the bare chat root when no params are given', () => {
    expect(buildChatHref({})).toBe('/chat/');
  });

  it('adds agentId', () => {
    expect(buildChatHref({ agentId: 'a1' })).toBe('/chat/?agentId=a1');
  });

  it('adds projectId when agentId is absent', () => {
    expect(buildChatHref({ projectId: 'p1' })).toBe('/chat/?projectId=p1');
  });

  it('agentId wins over projectId — a thread cannot be scoped to both', () => {
    expect(buildChatHref({ agentId: 'a1', projectId: 'p1' })).toBe('/chat/?agentId=a1');
  });

  it('adds conversationId alongside agentId', () => {
    expect(buildChatHref({ agentId: 'a1', conversationId: 'c1' })).toBe(
      '/chat/?agentId=a1&conversationId=c1',
    );
  });

  it('adds conversationId alongside projectId', () => {
    expect(buildChatHref({ projectId: 'p1', conversationId: 'c1' })).toBe(
      '/chat/?projectId=p1&conversationId=c1',
    );
  });

  it('ignores falsy/empty values', () => {
    expect(buildChatHref({ agentId: null, projectId: '', conversationId: undefined })).toBe('/chat/');
  });
});

describe('openFreshAgentChat / openFreshProjectChat', () => {
  beforeEach(() => {
    useChatStore.setState({ slots: {}, activeSlotId: null });
    window.history.replaceState(null, '', '/chat/');
  });

  it('openFreshAgentChat clears the active slot and navigates to the agent home', () => {
    const slotId = useChatStore.getState().createSlot('c1');
    useChatStore.getState().setActiveSlot(slotId);
    const router = { replace: vi.fn() };

    openFreshAgentChat('a1', router);

    expect(useChatStore.getState().activeSlotId).toBeNull();
    expect(router.replace).toHaveBeenCalledWith('/chat/?agentId=a1');
    expect(window.location.search).toBe('?agentId=a1');
  });

  it('openFreshProjectChat clears the active slot and navigates to the project home', () => {
    const slotId = useChatStore.getState().createSlot('c1');
    useChatStore.getState().setActiveSlot(slotId);
    const router = { replace: vi.fn() };

    openFreshProjectChat('p1', router);

    expect(useChatStore.getState().activeSlotId).toBeNull();
    expect(router.replace).toHaveBeenCalledWith('/chat/?projectId=p1');
    expect(window.location.search).toBe('?projectId=p1');
  });
});
