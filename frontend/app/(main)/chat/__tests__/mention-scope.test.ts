/**
 * The sources a question names with `@` scope that question only: the request carries them alongside what the chat
 * selected, and the chat's own selection is left as it was.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { ThreadMessageLike } from '@assistant-ui/react';

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

const { useChatStore } = await import('../store');
const { buildStreamChatRequestForSlot } = await import('../runtime');
const { queueSend, queueOf } = await import('../utils/queued-send');

const initialSettings = useChatStore.getState().settings;

const question = (mentionIds?: string[]): ThreadMessageLike => ({
  role: 'user',
  content: [{ type: 'text', text: 'what changed in Q3 Report' }],
  metadata: { custom: mentionIds ? { mentionIds } : {} },
});

beforeEach(() => {
  useChatStore.setState({
    slots: {},
    activeSlotId: null,
    projectScope: null,
    projectKnowledgeScope: null,
    agentKnowledgeScope: null,
    agentKnowledgeDefaults: { apps: [], kb: [] },
    collectionNamesCache: { r1: 'Q3 Report', a1: 'Drive' },
    collectionMetaCache: {
      r1: { name: 'Q3 Report', nodeType: 'record', connector: 'KB' },
      a1: { name: 'Drive', nodeType: 'app', connector: 'DRIVE' },
    },
    settings: { ...initialSettings, queryMode: 'chat', filters: { apps: ['a1'], kb: [] } },
  });
});

describe('a question that names a source', () => {
  it('asks it alongside the chat’s selection, marked as named, and leaves the selection as it was', () => {
    const slotId = useChatStore.getState().createSlot(null);
    const request = buildStreamChatRequestForSlot(slotId, 'what changed', question(['r1']));
    expect(request?.filters).toMatchObject({ apps: ['a1'], records: ['r1'] });
    expect(request?.appliedFilters?.records).toEqual([expect.objectContaining({ id: 'r1', name: 'Q3 Report', mentioned: true })]);
    expect(useChatStore.getState().settings.filters).toEqual({ apps: ['a1'], kb: [] });
  });

  it('the next question, naming nothing, goes with the chat’s selection only', () => {
    const slotId = useChatStore.getState().createSlot(null);
    buildStreamChatRequestForSlot(slotId, 'what changed', question(['r1']));
    const next = buildStreamChatRequestForSlot(slotId, 'and then?', question());
    expect(next?.filters.records ?? []).toEqual([]);
    expect(next?.filters.apps).toEqual(['a1']);
  });

  it('in an agent chat nobody narrowed, asks the named source alone', () => {
    useChatStore.setState({ agentKnowledgeDefaults: { apps: ['agent-app'], kb: [] } });
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'ag-1' });
    const request = buildStreamChatRequestForSlot(slotId, 'what changed', question(['r1']));
    expect(request?.filters).toMatchObject({ apps: [], kb: [], records: ['r1'] });
    expect(useChatStore.getState().agentKnowledgeScope).toBeNull();
  });

  it('a queued question keeps what it named, and goes with it', () => {
    const slotId = useChatStore.getState().createSlot(null);
    queueSend(slotId, { query: 'later', mentionIds: ['r1'] });
    const [queued] = queueOf(useChatStore.getState().slots[slotId]);
    expect(queued.mentionIds).toEqual(['r1']);
    const request = buildStreamChatRequestForSlot(slotId, 'later', undefined, undefined, { mentionIds: queued.mentionIds });
    expect(request?.filters.records).toEqual(['r1']);
  });
});
