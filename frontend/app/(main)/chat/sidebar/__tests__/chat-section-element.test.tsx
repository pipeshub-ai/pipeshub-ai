import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

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
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));

const { useChatStore } = await import('@/chat/store');
const { ChatSectionElement } = await import('../chat-section-element');

const initialState = useChatStore.getState();
const unread = () => [...useChatStore.getState().unreadConversationIds];

const conversation = { id: 'conv-1', title: 'Quarterly revenue', isOwner: false } as never;

function renderRow(isActive = false) {
  return renderInTheme(<ChatSectionElement conversation={conversation} isActive={isActive} onClick={vi.fn()} />);
}

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
