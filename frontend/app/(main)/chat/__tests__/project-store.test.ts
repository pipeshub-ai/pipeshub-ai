/**
 * Unit tests for the Projects slice of `useChatStore` and its interaction
 * with `selectPendingForSidebar` (project-scoped pending rows) and
 * `buildStreamChatRequestForSlot` (projectId forwarding for new chats).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

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

const { useChatStore, selectPendingForSidebar } = await import('../store');
const { buildStreamChatRequestForSlot } = await import('../runtime');

const initialSettings = useChatStore.getState().settings;

function resetStore() {
  useChatStore.setState({
    slots: {},
    activeSlotId: null,
    projects: [],
    isProjectsLoading: false,
    projectsError: null,
    projectsVersion: 0,
    activeProjectId: null,
    conversations: [],
    agentConversations: [],
    pendingConversations: {},
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

function makeProject(overrides: Partial<{ _id: string; name: string }> = {}) {
  return {
    _id: 'p1',
    orgId: 'org1',
    userId: 'u1',
    name: 'Q3 Plan',
    visibility: 'private' as const,
    chatSharing: 'private' as const,
    isPinned: false,
    isArchived: false,
    lastActivityAt: Date.now(),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    role: 'owner' as const,
    conversationCount: 0,
    ...overrides,
  };
}

describe('projects slice — list actions', () => {
  it('setProjects replaces the list', () => {
    const p = makeProject();
    useChatStore.getState().setProjects([p]);
    expect(useChatStore.getState().projects).toEqual([p]);
  });

  it('upsertProjectInList inserts a new project at the front', () => {
    const existing = makeProject({ _id: 'p0', name: 'Existing' });
    useChatStore.getState().setProjects([existing]);
    const created = makeProject({ _id: 'p1', name: 'New' });
    useChatStore.getState().upsertProjectInList(created);
    expect(useChatStore.getState().projects.map((p) => p._id)).toEqual(['p1', 'p0']);
  });

  it('upsertProjectInList replaces an existing project in place', () => {
    const original = makeProject({ name: 'Original' });
    useChatStore.getState().setProjects([original]);
    const renamed = { ...original, name: 'Renamed' };
    useChatStore.getState().upsertProjectInList(renamed);
    expect(useChatStore.getState().projects).toEqual([renamed]);
  });

  it('removeProjectFromList removes only the matching project', () => {
    const p1 = makeProject({ _id: 'p1' });
    const p2 = makeProject({ _id: 'p2' });
    useChatStore.getState().setProjects([p1, p2]);
    useChatStore.getState().removeProjectFromList('p1');
    expect(useChatStore.getState().projects).toEqual([p2]);
  });

  it('bumpProjectsVersion increments the counter', () => {
    useChatStore.getState().bumpProjectsVersion();
    useChatStore.getState().bumpProjectsVersion();
    expect(useChatStore.getState().projectsVersion).toBe(2);
  });

  it('setActiveProjectId stores the current project scope', () => {
    useChatStore.getState().setActiveProjectId('p1');
    expect(useChatStore.getState().activeProjectId).toBe('p1');
    useChatStore.getState().setActiveProjectId(null);
    expect(useChatStore.getState().activeProjectId).toBeNull();
  });
});

describe('moveConversationToProject', () => {
  it('sets projectId on the matching row in both conversation lists', () => {
    useChatStore.setState({
      conversations: [{ id: 'c1', title: 'Chat 1', createdAt: '', updatedAt: '', isShared: false, sharedWith: [] }],
      agentConversations: [{ id: 'c2', title: 'Chat 2', createdAt: '', updatedAt: '', isShared: false, sharedWith: [] }],
    });
    useChatStore.getState().moveConversationToProject('c1', 'p1');
    useChatStore.getState().moveConversationToProject('c2', 'p1');
    expect(useChatStore.getState().conversations[0].projectId).toBe('p1');
    expect(useChatStore.getState().agentConversations[0].projectId).toBe('p1');
  });

  it('unsets projectId when passed null', () => {
    useChatStore.setState({
      conversations: [
        { id: 'c1', title: 'Chat 1', createdAt: '', updatedAt: '', isShared: false, sharedWith: [], projectId: 'p1' },
      ],
    });
    useChatStore.getState().moveConversationToProject('c1', null);
    expect(useChatStore.getState().conversations[0].projectId).toBeUndefined();
  });

  it('leaves non-matching rows untouched', () => {
    useChatStore.setState({
      conversations: [
        { id: 'c1', title: 'Chat 1', createdAt: '', updatedAt: '', isShared: false, sharedWith: [] },
        { id: 'c2', title: 'Chat 2', createdAt: '', updatedAt: '', isShared: false, sharedWith: [] },
      ],
    });
    useChatStore.getState().moveConversationToProject('c1', 'p1');
    expect(useChatStore.getState().conversations[1].projectId).toBeUndefined();
  });
});

describe('selectPendingForSidebar — project scope', () => {
  function makePending(slotId: string) {
    return { slotId, title: null, isGenerating: true, createdAt: Date.now() };
  }

  it('excludes a project-linked new chat from the global (main sidebar) scope', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { projectId: 'p1' });
    const pending = { [slotId]: makePending(slotId) };
    const slots = useChatStore.getState().slots;
    const result = selectPendingForSidebar(pending, slots, new Set(), 'global');
    expect(result).toEqual([]);
  });

  it('includes a project-linked new chat in that project scope only', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { projectId: 'p1' });
    const pending = { [slotId]: makePending(slotId) };
    const slots = useChatStore.getState().slots;

    expect(selectPendingForSidebar(pending, slots, new Set(), { projectId: 'p1' })).toHaveLength(1);
    expect(selectPendingForSidebar(pending, slots, new Set(), { projectId: 'p2' })).toEqual([]);
  });

  it('still isolates agent-scoped pending rows from project scope', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'a1' });
    const pending = { [slotId]: makePending(slotId) };
    const slots = useChatStore.getState().slots;

    expect(selectPendingForSidebar(pending, slots, new Set(), { projectId: 'p1' })).toEqual([]);
    expect(selectPendingForSidebar(pending, slots, new Set(), { agentId: 'a1' })).toHaveLength(1);
  });
});

describe('buildStreamChatRequestForSlot — projectId forwarding', () => {
  it('includes projectId for a brand-new conversation in a project', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { projectId: 'p1' });
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request?.projectId).toBe('p1');
  });

  it('omits projectId once the slot has a real conversationId (existing session is the source of truth)', () => {
    const slotId = useChatStore.getState().createSlot('c1');
    useChatStore.getState().updateSlot(slotId, { projectId: 'p1' });
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toHaveProperty('projectId');
  });

  it('omits projectId when the slot has none', () => {
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'hello');
    expect(request).not.toHaveProperty('projectId');
  });
});
