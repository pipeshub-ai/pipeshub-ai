/**
 * CJ14-FU-2: the main sidebar folds Recents by default, so a chat that needs the person showed no mark until it was
 * opened. Folded, the Recents header carries the strongest mark of the chats it holds (needs you over working over
 * finished and not seen), from the list the sidebar already loaded: no added request.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
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
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const { useChatStore } = await import('@/chat/store');
const { ChatSections } = await import('../chat-sections');
const initialState = useChatStore.getState();

type State = NonNullable<Conversation['harnessState']>;
const state = (over: Partial<State>): State => ({ activeRun: null, runState: null, needsYou: false, finishedUnseen: false, seenUpTo: null, ...over });

function chat(id: string, harnessState?: State): Conversation {
  return { id, title: id, createdAt: '2026-01-01T00:00:00.000Z', updatedAt: '2026-01-01T00:00:00.000Z', isShared: false, sharedWith: [], isOwner: true, ...(harnessState ? { harnessState } : {}) };
}

function render(conversations: Conversation[], shared: Conversation[] = []) {
  useChatStore.setState({ conversations, sharedConversations: shared, isConversationsLoading: false, conversationsError: null });
  renderInTheme(<ChatSections onOpenMoreChats={vi.fn()} />);
  return screen.getByTestId('recents-toggle');
}

beforeEach(() => {
  installBrowserShims();
  useChatStore.setState({ ...initialState });
});
afterEach(() => cleanup());

describe('CJ14-FU-2 a folded Recents keeps its marks', () => {
  it('says a hidden chat needs you, over one working and one finished', () => {
    const toggle = render(
      [chat('a', state({ runState: 'working' })), chat('b', state({ finishedUnseen: true }))],
      [chat('c', state({ runState: 'needs_you', needsYou: true }))],
    );
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.querySelector('[data-testid="chat-dot"]')?.getAttribute('data-dot')).toBe('needsYou');
    expect(screen.getByRole('button', { name: 'Recents Needs you' })).toBe(toggle);
  });

  it('shows working, then nothing for the chat open now, and drops the header mark once Recents is open', () => {
    const toggle = render([chat('a', state({ runState: 'working' })), chat('open', state({ finishedUnseen: true }))]);
    expect(toggle.querySelector('[data-testid="chat-dot"]')?.getAttribute('data-dot')).toBe('working');
    // A native button: the browser turns Enter and Space into this click.
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.querySelector('[data-testid="chat-dot"]')).toBeNull();
    expect(screen.getAllByTestId('chat-dot').map((d) => d.getAttribute('data-dot'))).toEqual(['working']);
  });

  it('has no mark when no chat holds one', () => {
    const toggle = render([chat('a'), chat('open', state({ finishedUnseen: true }))]);
    expect(toggle.querySelector('[data-testid="chat-dot"]')).toBeNull();
  });

  it('strongestDot ranks needs you, working, then finished and not seen', () => {
    expect(strongestDot([null, { dot: 'finished' }, { dot: 'finished', unseen: true }])).toEqual({ dot: 'finished', unseen: true });
    expect(strongestDot([{ dot: 'working' }, { dot: 'needsYou' }, { dot: 'finished', unseen: true }])).toEqual({ dot: 'needsYou' });
    expect(strongestDot([])).toBeNull();
  });
});
