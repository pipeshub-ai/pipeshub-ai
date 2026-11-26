import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

installMemoryStorage();

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/chat/',
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const { useChatStore } = await import('@/chat/store');
const { ChatSectionElement, GeneratingTitleItem } = await import('../chat-section-element');

const initialState = useChatStore.getState();
const unread = () => [...useChatStore.getState().unreadConversationIds];

const conversation = { id: 'conv-1', title: 'Quarterly revenue', isOwner: false } as never;

function renderRow(isActive = false) {
  return renderInTheme(<ChatSectionElement conversation={conversation} isActive={isActive} onClick={vi.fn()} />);
}

const dot = () => screen.queryByText('New answer');

beforeEach(() => {
  installBrowserShims();
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, unreadConversationIds: new Set() });
});

afterEach(() => cleanup());

describe('a chat row in the sidebar', () => {
  it('shows no dot for a chat with nothing new', () => {
    renderRow();

    expect(screen.getByText('Quarterly revenue')).toBeTruthy();
    expect(dot()).toBeNull();
  });

  it('shows a dot, announced as a new answer, when an answer finished out of view', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    renderRow();

    expect(screen.getByText('Quarterly revenue')).toBeTruthy();
    expect(dot()).not.toBeNull();
  });

  it('shows the dot only on the chat that has a new answer', () => {
    useChatStore.getState().markConversationUnread('conv-other');

    renderRow();

    expect(dot()).toBeNull();
  });

  it('adds the dot when an answer finishes while the row is already on screen', () => {
    renderRow();
    expect(dot()).toBeNull();

    act(() => useChatStore.getState().markConversationUnread('conv-1'));

    expect(dot()).not.toBeNull();
  });

  it('removes the dot once the user opens the chat', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    const { rerender } = renderRow(false);
    expect(dot()).not.toBeNull();

    rerender(<ChatSectionElement conversation={conversation} isActive onClick={vi.fn()} />);

    expect(dot()).toBeNull();
    expect(unread()).toEqual([]);
  });

  it('never shows a dot on the chat the user is already in', () => {
    useChatStore.getState().markConversationUnread('conv-1');

    renderRow(true);

    expect(dot()).toBeNull();
    expect(unread()).toEqual([]);
  });

  it('keeps the other unread chats unread when one is opened', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    useChatStore.getState().markConversationUnread('conv-2');

    renderRow(true);

    expect(unread()).toEqual(['conv-2']);
  });

  it('shows the running indicator, not the dot, while that chat is answering again', () => {
    useChatStore.getState().markConversationUnread('conv-1');
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, { isStreaming: true });

    renderRow();

    expect(dot()).toBeNull();
  });
});

describe('D10: the row this tab streams shows Needs you over its spinner (CJFIX-A F2)', () => {
  const ask = { interactionId: 'ix-1', runId: 'r-1', toolName: 'mcp_e2esaas_create_ticket', status: 'pending' } as never;
  const mark = () => document.querySelector('[data-testid="chat-dot"]')?.getAttribute('data-dot') ?? null;

  it('a new chat still streaming, before the list has it: working, then Needs you once its stream holds an ask', () => {
    const slotId = useChatStore.getState().createSlot('conv-new');
    useChatStore.getState().updateSlot(slotId, { isStreaming: true });
    useChatStore.setState((s) => ({ pendingConversations: { ...s.pendingConversations, [slotId]: { slotId, title: 'File a ticket', isGenerating: true, createdAt: 1 } as never } }));
    renderInTheme(<GeneratingTitleItem slotId={slotId} />);
    expect(mark()).toBe('working');

    act(() => useChatStore.getState().updateSlot(slotId, { pendingApprovals: [ask] }));

    expect(mark()).toBe('needsYou');
    expect(screen.getByText('Needs you')).toBeTruthy();
  });

  it('a listed chat whose stream here holds an ask is Needs you before the server’s state says so', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, { isStreaming: true, pendingApprovals: [ask] });
    renderRow();
    expect(mark()).toBe('needsYou');
  });
});

describe('where a chat row opens', () => {
  const hrefOf = (conv: unknown, agentId?: string) => {
    renderInTheme(<ChatSectionElement conversation={conv as never} isActive={false} onClick={vi.fn()} agentId={agentId} />);
    return screen.getByRole('link').getAttribute('href');
  };

  it('opens a plain chat through the plain chat URL', () => {
    expect(hrefOf({ id: 'conv-1', title: 'Quarterly revenue', isOwner: true })).toBe('/chat?conversationId=conv-1');
  });

  it('opens an agent chat shared with the user under the agent it belongs to', () => {
    expect(hrefOf({ id: 'conv-2', title: 'Roadmap', isOwner: false, agentKey: 'agent-7' })).toBe(
      '/chat?agentId=agent-7&conversationId=conv-2',
    );
  });

  it('prefers the agent of the sidebar section it is listed in', () => {
    expect(hrefOf({ id: 'conv-3', title: 'Roadmap', isOwner: true, agentKey: 'agent-9' }, 'agent-7')).toBe(
      '/chat?agentId=agent-7&conversationId=conv-3',
    );
  });
});

describe('who shared a chat, and whether it is an agent chat', () => {
  const sharedBy = { userId: 'u-1', name: 'Pranjal' };

  it('says "Shared by" for a plain chat someone shared', () => {
    renderInTheme(
      <ChatSectionElement conversation={{ id: 'c1', title: 'Plain', isOwner: false, sharedBy } as never} isActive={false} onClick={vi.fn()} />,
    );
    expect(screen.getByText('Shared by Pranjal')).toBeTruthy();
  });

  it('marks an agent chat someone shared with the agent symbol, in the global list', () => {
    renderInTheme(
      <ChatSectionElement conversation={{ id: 'c2', title: 'Roadmap', isOwner: false, sharedBy, agentKey: 'agent-7' } as never} isActive={false} onClick={vi.fn()} />,
    );
    const symbol = screen.getByRole('img', { name: 'Agent chat' });
    const name = screen.getByText('Shared by Pranjal');
    // The sharer's name comes first and the symbol after it, not the other way round.
    expect(name.contains(symbol)).toBe(true);
    expect(name.firstChild?.textContent).toBe('Shared by Pranjal');
    expect(name.lastElementChild).toBe(symbol);
  });

  it('names the symbol "Agent chat" in a tooltip on hover', async () => {
    renderInTheme(
      <ChatSectionElement conversation={{ id: 'c2', title: 'Roadmap', isOwner: false, sharedBy, agentKey: 'agent-7' } as never} isActive={false} onClick={vi.fn()} />,
    );
    fireEvent.pointerMove(screen.getByRole('img', { name: 'Agent chat' }));
    expect((await screen.findAllByText('Agent chat')).length).toBeGreaterThan(0);
  });

  it('shows no agent symbol on a plain shared chat', () => {
    renderInTheme(
      <ChatSectionElement conversation={{ id: 'c1', title: 'Plain', isOwner: false, sharedBy } as never} isActive={false} onClick={vi.fn()} />,
    );
    expect(screen.queryByRole('img', { name: 'Agent chat' })).toBeNull();
  });

  it('shows no agent symbol inside that agent’s own sidebar, where every chat is an agent chat', () => {
    renderInTheme(
      <ChatSectionElement conversation={{ id: 'c3', title: 'Roadmap', isOwner: false, sharedBy, agentKey: 'agent-7' } as never} isActive={false} onClick={vi.fn()} agentId="agent-7" />,
    );
    expect(screen.getByText('Shared by Pranjal')).toBeTruthy();
    expect(screen.queryByRole('img', { name: 'Agent chat' })).toBeNull();
  });
});

describe('a chat row’s run mark from the server (classic jobs D10)', () => {
  const state = (over: Record<string, unknown> = {}) => ({ activeRun: null, runState: null, needsYou: false, finishedUnseen: false, seenUpTo: null, ...over });
  const rowWith = (harnessState: unknown, isActive = false) =>
    renderInTheme(
      <ChatSectionElement conversation={{ id: 'conv-9', title: 'Q3 report', isOwner: false, harnessState } as never} isActive={isActive} onClick={vi.fn()} />,
    );
  const mark = () => screen.queryByTestId('chat-dot');

  it('marks a chat that waits on the person amber, named "Needs you", over working and finished', () => {
    rowWith(state({ needsYou: true, runState: 'needs_you', activeRun: { runId: 'r1', status: 'waiting_approval' }, finishedUnseen: true }));
    expect(mark()?.getAttribute('data-dot')).toBe('needsYou');
    expect(mark()?.textContent).toBe('Needs you');
  });

  it('shows the working spinner for a run this tab never streamed', () => {
    rowWith(state({ runState: 'working', activeRun: { runId: 'r1', status: 'running' } }));
    expect(mark()?.getAttribute('data-dot')).toBe('working');
    expect(mark()?.textContent).toBe('Working');
  });

  it('shows the finished dot for a run that ended unseen, and none on the chat open now', () => {
    rowWith(state({ finishedUnseen: true }));
    expect(mark()?.getAttribute('data-dot')).toBe('finished');
    expect(mark()?.textContent).toBe('Finished since you last looked');
    cleanup();
    rowWith(state({ finishedUnseen: true }), true);
    expect(mark()).toBeNull();
  });

  it('keeps needs you on the open chat, since it still waits there', () => {
    rowWith(state({ needsYou: true }), true);
    expect(mark()?.getAttribute('data-dot')).toBe('needsYou');
  });

  it('merges the server’s unseen with this tab’s new answer into one mark', () => {
    useChatStore.getState().markConversationUnread('conv-9');
    rowWith(state({ finishedUnseen: true }));
    expect(screen.getAllByTestId('chat-dot')).toHaveLength(1);
    expect(mark()?.textContent).toBe('Finished since you last looked');
  });

  it('shows nothing for a quiet chat', () => {
    rowWith(state());
    expect(mark()).toBeNull();
  });
});
