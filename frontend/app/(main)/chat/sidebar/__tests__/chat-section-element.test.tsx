import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { CHAT_ITEM_HEIGHT } from '@/app/components/sidebar/constants';

installMemoryStorage();

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
const urlReads = vi.hoisted(() => ({ n: 0 }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => {
    urlReads.n += 1;
    return new URLSearchParams();
  },
  usePathname: () => '/chat/',
}));
// Only the pin draws, so its colour, fill and turn can be read.
vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name, color, variant }: { name: string; color?: string; variant?: string }) =>
    name === 'keep' ? <i data-icon={name} data-color={color} data-variant={variant} /> : null,
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));

const { useChatStore } = await import('@/chat/store');
const { ChatSectionElement, GeneratingTitleItem } = await import('../chat-section-element');

const initialState = useChatStore.getState();
const unread = () => [...useChatStore.getState().unreadConversationIds];

const conversation = { id: 'conv-1', title: 'Quarterly revenue', isOwner: false } as never;

function renderRow(isActive = false) {
  return renderInTheme(<ChatSectionElement conversation={conversation} isActive={isActive} onClick={vi.fn()} />);
}

// The row's dot for a new answer, named by its words.
const dot = () => screen.queryByRole('img', { name: 'New answer' });

beforeEach(() => {
  installBrowserShims();
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, unreadConversationIds: new Set() });
});

afterEach(() => cleanup());

describe('a chat row in the sidebar', () => {
  it('does not follow the page URL, so a chat getting its id does not draw every row again', () => {
    urlReads.n = 0;
    renderRow();
    expect(urlReads.n).toBe(0);
  });

  it('is not drawn again when another chat gets its title', () => {
    let commits = 0;
    renderInTheme(
      <React.Profiler id="row" onRender={() => { commits += 1; }}>
        <ChatSectionElement conversation={conversation} isActive={false} onClick={vi.fn()} />
      </React.Profiler>,
    );
    const before = commits;
    act(() => useChatStore.setState({ newlyResolvedIds: new Set(['conv-other']) }));
    expect(commits).toBe(before);
  });

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
    expect(screen.getByRole('img', { name: 'Working in the background' })).toBeTruthy();
  });

  it('marks a running chat with a pulsing dot, not a spinner: "Working" on the open chat, "Working in the background" elsewhere', () => {
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.getState().updateSlot(slotId, { isStreaming: true });

    const { rerender } = renderRow(false);
    const running = screen.getByRole('img', { name: 'Working in the background' });
    expect(running.className).toBe('sidebar-activity-dot');
    expect(running.hasAttribute('data-live')).toBe(true);
    expect(screen.queryByRole('status')).toBeNull();

    rerender(<ChatSectionElement conversation={conversation} isActive onClick={vi.fn()} />);
    expect(screen.getByRole('img', { name: 'Working' })).toBeTruthy();
  });
});

describe('a pinned chat row', () => {
  const pinned = { id: 'conv-3', title: 'Roadmap', isOwner: true, isPinned: true } as never;
  const mark = () => document.querySelector('[data-pinned-mark]');
  const row = (conv: unknown, agentId?: string) => (
    <Theme>
      <ChatSectionElement conversation={conv as never} isActive={false} onClick={vi.fn()} agentId={agentId} />
    </Theme>
  );

  it('wears the pin right after its title, hidden from screen readers, and its name says pinned', () => {
    render(row(pinned));

    // Browsers, and this one, put a space before a visually hidden word: read aloud, it is "Roadmap, pinned".
    expect(screen.getByRole('link', { name: /^Roadmap ?, pinned$/ })).toBeTruthy();
    expect(mark()?.getAttribute('aria-hidden')).toBe('true');
    expect(screen.getByText('Roadmap').nextElementSibling).toBe(mark());
  });

  it('the pin is the brand green, filled and tilted, not the grey of secondary text', () => {
    render(row(pinned));

    const icon = mark()!.querySelector('[data-icon="keep"]')!;
    expect(icon.getAttribute('data-color')).toBe('var(--accent-a11)');
    expect(icon.getAttribute('data-variant')).toBe('filled');
    expect((mark() as HTMLElement).style.transform).toBe('rotate(45deg)');
  });

  it('an unpinned row has no pin, and Pin or Unpin shows or drops it at once', () => {
    const { rerender } = render(row({ ...(pinned as object), isPinned: false }));
    expect(mark()).toBeNull();
    expect(screen.getByRole('link', { name: 'Roadmap' })).toBeTruthy();

    rerender(row(pinned));
    expect(mark()).not.toBeNull();

    rerender(row({ ...(pinned as object), isPinned: undefined }));
    expect(mark()).toBeNull();
  });

  it('an agent chat, which has no pin, never wears it', () => {
    render(row(pinned, 'agent-7'));

    expect(mark()).toBeNull();
  });

  it('keeps the pin right after the title while the chat answers, before the running dot', () => {
    const slotId = useChatStore.getState().createSlot('conv-3');
    useChatStore.getState().updateSlot(slotId, { isStreaming: true });
    render(row(pinned));

    const running = screen.getByRole('img', { name: 'Working in the background' });
    expect(screen.getByText('Roadmap').nextElementSibling).toBe(mark());
    expect(mark()!.compareDocumentPosition(running) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
    // Working is the row's pulsing dot, named by its words.
    expect(screen.getByRole('img', { name: 'Working in the background' })).toBeTruthy();
    expect(mark()).toBeNull();

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

describe('a chat someone shared', () => {
  const sharedBy = { userId: 'u-1', name: 'Pranjal' };

  // As the design draws it: the title, and nothing under it.
  it.each([
    ['a plain chat', { id: 'c1', title: 'Roadmap', isOwner: false, sharedBy }, undefined],
    ['an agent chat, in the global list', { id: 'c2', title: 'Roadmap', isOwner: false, sharedBy, agentKey: 'agent-7' }, undefined],
    ['an agent chat, in that agent’s own sidebar', { id: 'c3', title: 'Roadmap', isOwner: false, sharedBy, agentKey: 'agent-7' }, 'agent-7'],
  ])('%s is one line, its title, with no "Shared by" under it', (_, conv, agentId) => {
    renderInTheme(<ChatSectionElement conversation={conv as never} isActive={false} onClick={vi.fn()} agentId={agentId} />);

    const link = screen.getByRole('link', { name: 'Roadmap' });
    expect(screen.queryByText(/Shared by/)).toBeNull();
    expect(link.style.height).toBe(`${CHAT_ITEM_HEIGHT}px`);
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

  // Working and finished are the row's own dots (a pulsing one, and the new-answer one), named by their words.
  const finished = () => screen.queryByRole('img', { name: 'Finished since you last looked' });

  it('shows the working mark for a run this tab never streamed', () => {
    rowWith(state({ runState: 'working', activeRun: { runId: 'r1', status: 'running' } }));
    expect(screen.getByRole('img', { name: 'Working in the background' })).toBeTruthy();
    expect(mark()).toBeNull();
  });

  it('shows the finished dot for a run that ended unseen, and none on the chat open now', () => {
    rowWith(state({ finishedUnseen: true }));
    expect(finished()).not.toBeNull();
    cleanup();
    rowWith(state({ finishedUnseen: true }), true);
    expect(finished()).toBeNull();
    expect(screen.queryByTestId('unread-dot')).toBeNull();
  });

  it('keeps needs you on the open chat, since it still waits there', () => {
    rowWith(state({ needsYou: true }), true);
    expect(mark()?.getAttribute('data-dot')).toBe('needsYou');
  });

  it('merges the server’s unseen with this tab’s new answer into one mark', () => {
    useChatStore.getState().markConversationUnread('conv-9');
    rowWith(state({ finishedUnseen: true }));
    expect(screen.getAllByTestId('unread-dot')).toHaveLength(1);
    expect(finished()).not.toBeNull();
  });

  it('shows nothing for a quiet chat', () => {
    rowWith(state());
    expect(mark()).toBeNull();
  });
});
