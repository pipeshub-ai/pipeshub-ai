/**
 * CJ14-FU-2: a chat that needs the person says so even when its row is out of sight. The sidebar shows each list's pins
 * and newest chats, then "More" for the rest, so a chat past that cap showed no mark until More was opened. Each More
 * carries the strongest mark of the chats it hides (needs you over working over finished and not seen), from the list
 * the sidebar already loaded: no added request.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { Conversation } from '@/chat/types';
import { strongestDot } from '@/lib/harness-jobs/jobs-list';

installMemoryStorage();

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams('conversationId=open'),
  usePathname: () => '/chat/',
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const { useChatStore } = await import('@/chat/store');
const { ChatSections } = await import('../chat-sections');
const initialState = useChatStore.getState();

type State = NonNullable<Conversation['harnessState']>;
const state = (over: Partial<State>): State => ({ activeRun: null, runState: null, needsYou: false, finishedUnseen: false, seenUpTo: null, ...over });

function chat(id: string, harnessState?: State): Conversation {
  return { id, title: id, createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z', isShared: false, sharedWith: [], isOwner: true, ...(harnessState ? { harnessState } : {}) };
}

/** `n` quiet chats, newest first: the rows a list shows before its More (three shared, five of yours). */
const quiet = (prefix: string, n: number) => Array.from({ length: n }, (_, i) => chat(`${prefix}${String(i + 1)}`));

function render(conversations: Conversation[], shared: Conversation[] = []) {
  useChatStore.setState({ conversations, sharedConversations: shared, isConversationsLoading: false, conversationsError: null });
  renderInTheme(<ChatSections onOpenMoreChats={vi.fn()} />);
}

/** Each list's More row, top to bottom: Shared chats', then Your chats'. */
const moreRows = () => screen.getAllByText('More').map((label) => label.closest('[role="button"]') as HTMLElement);
const markOn = (row: HTMLElement) => row.querySelector('[data-testid="chat-dot"]')?.getAttribute('data-dot') ?? null;

beforeEach(() => {
  installBrowserShims();
  useChatStore.setState({ ...initialState });
});
afterEach(() => cleanup());

describe('CJ14-FU-2 each More keeps the marks of the chats it hides', () => {
  it('says a hidden chat needs you, over one working and one finished; the other list’s More has only its own', () => {
    render(
      [...quiet('y', 5), chat('a', state({ runState: 'working' })), chat('b', state({ finishedUnseen: true })), chat('c', state({ runState: 'needs_you', needsYou: true }))],
      [...quiet('s', 3), chat('d', state({ finishedUnseen: true }))],
    );
    const [shared, yours] = moreRows();
    expect(markOn(yours)).toBe('needsYou');
    expect(screen.getByRole('button', { name: 'More Needs you' })).toBe(yours);
    expect(markOn(shared)).toBe('finished');
  });

  it('shows working, then nothing for the chat open now, and drops the mark once the chat it stood for is shown', () => {
    render([...quiet('y', 5), chat('a', state({ runState: 'working' })), chat('open', state({ finishedUnseen: true }))]);
    expect(markOn(moreRows()[0])).toBe('working');
    // The run moves its chat up the list: its row says it is working, and More, left with the open chat, says nothing.
    act(() => useChatStore.setState({ conversations: [chat('a', state({ runState: 'working' })), ...quiet('y', 5), chat('open', state({ finishedUnseen: true }))] }));
    expect(markOn(moreRows()[0])).toBeNull();
    expect(screen.queryAllByTestId('chat-dot')).toEqual([]);
    expect(screen.getByRole('img', { name: 'Working in the background' })).toBeTruthy();
  });

  it('has no mark when no hidden chat holds one', () => {
    render([...quiet('y', 5), chat('a'), chat('open', state({ finishedUnseen: true }))]);
    expect(markOn(moreRows()[0])).toBeNull();
  });

  describe('with the agent harness off, when the server sends no run state', () => {
    it('says working for a hidden chat this tab is answering, as its row would', () => {
      render([...quiet('y', 5), chat('a')]);
      expect(markOn(moreRows()[0])).toBeNull();
      act(() => {
        const slotId = useChatStore.getState().createSlot('a');
        useChatStore.getState().updateSlot(slotId, { isStreaming: true });
      });
      expect(markOn(moreRows()[0])).toBe('working');
    });

    it('says a hidden chat needs you while its question card waits on the person', () => {
      render([...quiet('y', 5), chat('a')]);
      act(() => {
        const slotId = useChatStore.getState().createSlot('a');
        useChatStore.getState().updateSlot(slotId, {
          pendingAskUserQuestion: { status: 'pending', assistantMessageId: 'r1' } as never,
          messages: [{ id: 'r1', role: 'assistant', content: [] } as never],
        });
      });
      expect(markOn(moreRows()[0])).toBe('needsYou');
    });

    it('says finished for a hidden chat whose answer this tab saw finish out of view, until it is read', () => {
      render([...quiet('y', 5), chat('a')]);
      act(() => useChatStore.setState({ unreadConversationIds: new Set(['a']) }));
      expect(markOn(moreRows()[0])).toBe('finished');
      act(() => useChatStore.getState().markConversationRead('a'));
      expect(markOn(moreRows()[0])).toBeNull();
    });
  });

  it('strongestDot ranks needs you, working, then finished and not seen', () => {
    expect(strongestDot([null, { dot: 'finished' }, { dot: 'finished', unseen: true }])).toEqual({ dot: 'finished', unseen: true });
    expect(strongestDot([{ dot: 'working' }, { dot: 'needsYou' }, { dot: 'finished', unseen: true }])).toEqual({ dot: 'needsYou' });
    expect(strongestDot([])).toBeNull();
  });
});
