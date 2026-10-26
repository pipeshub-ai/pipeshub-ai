/**
 * An answer that finishes while the user is looking at something else: when it
 * is announced, when it is not, and where the toast takes them.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

const { useChatStore } = await import('../../store');
const { useToastStore } = await import('@/lib/store/toast-store');
const { notifyAnswerFinishedInBackground } = await import('../background-finish');

const initialState = useChatStore.getState();

const toasts = () => useToastStore.getState().toasts;
const unread = () => [...useChatStore.getState().unreadConversationIds];

function open(path: string) {
  window.history.replaceState(null, '', path);
}

/** A finished slot for `convId`, with another chat open unless `active` says otherwise. */
function seed(convId: string | null, overrides: Record<string, unknown> = {}, active: 'other' | 'this' | 'none' = 'other') {
  const store = useChatStore.getState();
  const slotId = store.createSlot(convId);
  const otherId = store.createSlot('conv-other');
  store.updateSlot(slotId, overrides);
  useChatStore.setState({
    activeSlotId: active === 'this' ? slotId : active === 'other' ? otherId : null,
  });
  return slotId;
}

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, conversations: [], agentConversations: [], unreadConversationIds: new Set() });
  useToastStore.getState().clearAll();
  open('/chat/');
});

afterEach(() => open('/'));

describe('an answer that finishes in a chat the user is not looking at', () => {
  it('shows a toast with the chat title that opens the chat', () => {
    useChatStore.setState({ conversations: [{ id: 'conv-1', title: 'Quarterly revenue' } as never] });
    const slotId = seed('conv-1');

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()).toHaveLength(1);
    const [t] = toasts();
    expect(t.title).toBe('Answer ready');
    expect(t.description).toBe('Quarterly revenue');
    expect(t.action).toEqual({ label: 'Open chat', href: '/chat/?conversationId=conv-1' });
  });

  it('marks the chat unread', () => {
    const slotId = seed('conv-1');

    notifyAnswerFinishedInBackground(slotId, false);

    expect(unread()).toEqual(['conv-1']);
  });

  it('shows no description when the chat has no known title', () => {
    const slotId = seed('conv-1');

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()[0].description).toBeUndefined();
  });

  it('does not use a blank title as the description', () => {
    useChatStore.setState({ conversations: [{ id: 'conv-1', title: '   ' } as never] });
    const slotId = seed('conv-1');

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()[0].description).toBeUndefined();
  });

  it("finds the title of an agent's chat", () => {
    useChatStore.setState({ agentConversations: [{ id: 'conv-1', title: 'Agent thread' } as never] });
    const slotId = seed('conv-1', { threadAgentId: 'agent-9' });

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()[0].description).toBe('Agent thread');
  });

  it("opens an agent's chat inside that agent", () => {
    const slotId = seed('conv-1', { threadAgentId: 'agent-9' });

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()[0].action?.href).toBe('/chat/?agentId=agent-9&conversationId=conv-1');
  });

  it("opens a project's chat inside that project", () => {
    const slotId = seed('conv-1', { projectId: 'proj-3' });

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()[0].action?.href).toBe('/chat/?projectId=proj-3&conversationId=conv-1');
  });

  it('is announced when no chat is open at all', () => {
    const slotId = seed('conv-1', {}, 'none');

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()).toHaveLength(1);
    expect(unread()).toEqual(['conv-1']);
  });

  it('is announced when the chat is open but the user has left the chat area', () => {
    open('/knowledge-base/');
    const slotId = seed('conv-1', {}, 'this');

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()).toHaveLength(1);
    expect(unread()).toEqual(['conv-1']);
  });

  it('announces each chat that finishes', () => {
    notifyAnswerFinishedInBackground(seed('conv-1'), false);
    notifyAnswerFinishedInBackground(seed('conv-2'), false);

    expect(toasts().map((t) => t.action?.href).sort()).toEqual([
      '/chat/?conversationId=conv-1',
      '/chat/?conversationId=conv-2',
    ]);
    expect(unread().sort()).toEqual(['conv-1', 'conv-2']);
  });

  it('dismisses itself after a while instead of waiting to be closed', () => {
    notifyAnswerFinishedInBackground(seed('conv-1'), false);

    expect(toasts()[0].duration).toBe(8000);
  });
});

describe('an answer that is not announced', () => {
  it('stays quiet when the user is looking at that chat', () => {
    const slotId = seed('conv-1', {}, 'this');

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()).toHaveLength(0);
    expect(unread()).toEqual([]);
  });

  it('stays quiet when the user stopped the run', () => {
    const slotId = seed('conv-1');

    notifyAnswerFinishedInBackground(slotId, true);

    expect(toasts()).toHaveLength(0);
    expect(unread()).toEqual([]);
  });

  it('stays quiet for a chat that has no saved conversation yet', () => {
    const slotId = seed(null);

    notifyAnswerFinishedInBackground(slotId, false);

    expect(toasts()).toHaveLength(0);
    expect(unread()).toEqual([]);
  });

  it('stays quiet for a chat that was closed in the meantime', () => {
    notifyAnswerFinishedInBackground('slot-that-was-evicted', false);

    expect(toasts()).toHaveLength(0);
    expect(unread()).toEqual([]);
  });
});
