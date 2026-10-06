/**
 * The message list of a collaborative chat is a log of Slack-style rows; a solo chat, or a build with the flag off,
 * keeps the one-block-per-turn list it always had.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

type ThreadMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: { type: 'text'; text: string }[];
  metadata?: { custom?: Record<string, unknown> };
};
const thread = vi.hoisted(() => ({ messages: [] as ThreadMessage[], append: vi.fn() }));

vi.mock('@assistant-ui/react', () => ({
  useThread: () => ({ messages: thread.messages }),
  useThreadRuntime: () => ({ append: thread.append }),
}));
vi.mock('../../../streaming', () => ({ loadOlderMessagesForSlot: vi.fn() }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div role="status">Loading</div> }));
vi.mock('../response-tabs/citations', async () => {
  const utils = await vi.importActual<typeof import('../response-tabs/citations/utils')>('../response-tabs/citations/utils');
  const control = await vi.importActual<typeof import('../response-tabs/citations/citation-popover-control')>(
    '../response-tabs/citations/citation-popover-control',
  );
  return {
    emptyCitationMaps: utils.emptyCitationMaps,
    isCitationPopoverKeyStillValid: control.isCitationPopoverKeyStillValid,
    useCitationActions: () => ({}),
  };
});
vi.mock('../response-tabs/citations/inline-citation-popover-host', () => ({ InlineCitationPopoverHost: () => null }));
vi.mock('../chat-response', () => ({
  formatMessageTime: () => '',
  ChatResponse: (props: { question: string; rowMode?: string; replyingTo?: { displayName: string | null } | null; showHeader?: boolean }) => (
    <div
      data-testid="response"
      data-mode={props.rowMode ?? 'both'}
      data-header={props.showHeader === false ? 'no' : 'yes'}
      data-replying-to={props.replyingTo === undefined ? '' : (props.replyingTo?.displayName ?? 'former')}
    >
      {props.question}
    </div>
  ),
}));

import { MessageList } from '../message-list';
import { useChatStore } from '../../../store';
import { useUserStore } from '@/lib/store/user-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';

const initialChatState = useChatStore.getState();
const alice = { userId: 'a', displayName: 'Alex' };
const bob = { userId: 'b', displayName: 'Bob' };

const user = (id: string, text: string, createdAt: string, author = alice): ThreadMessage => ({
  id, role: 'user', content: [{ type: 'text', text }], metadata: { custom: { createdAt, author } },
});
const reply = (id: string, text: string, createdAt: string): ThreadMessage => ({
  id, role: 'assistant', content: [{ type: 'text', text }], metadata: { custom: { messageId: id, createdAt } },
});
const note = (id: string, text: string, createdAt: string, author = bob): ThreadMessage => ({
  id, role: 'system', content: [{ type: 'text', text }], metadata: { custom: { messageType: 'note', createdAt, author } },
});

function open(collaborative: boolean, flag = true) {
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: flag } });
  useUserStore.setState({ profile: { userId: 'a' } as never });
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-1');
  store.updateSlot(slotId, {
    isInitialized: true,
    access: { role: 'owner', isOwner: true, accessLevel: 'owner', canSend: true, canManage: true, canInvite: true, isCollaborative: collaborative },
  });
  store.setActiveSlot(slotId);
}

const renderList = () => render(<Theme><MessageList /></Theme>);

beforeEach(() => {
  useChatStore.setState(initialChatState, true);
  thread.messages = [];
  vi.stubGlobal('requestAnimationFrame', () => 0);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  HTMLElement.prototype.scrollTo = (() => {}) as HTMLElement['scrollTo'];
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useFeatureFlagsStore.setState({ flags: null });
});

const day = (d: number, h: number, m: number) => new Date(2026, 8, d, h, m).toISOString();

describe('collaborative chat', () => {
  it('is a polite log with a question row and a reply row per turn', () => {
    open(true);
    thread.messages = [user('u1', 'tell me a joke', day(18, 10, 0)), reply('a1', 'Knock knock', day(18, 10, 1))];
    renderList();
    const log = screen.getByRole('log');
    expect(log.getAttribute('aria-live')).toBe('polite');
    expect(log.getAttribute('aria-label')).toBe('Conversation');
    expect(within(log).getAllByTestId('response').map((r) => r.getAttribute('data-mode'))).toEqual(['human', 'reply']);
  });

  it('puts a day divider above each new day', () => {
    open(true);
    thread.messages = [
      user('u1', 'first', day(16, 10, 0)), reply('a1', 'one', day(16, 10, 1)),
      user('u2', 'second', day(17, 9, 0)), reply('a2', 'two', day(17, 9, 1)),
    ];
    renderList();
    expect(screen.getAllByTestId('day-divider')).toHaveLength(2);
  });

  it('draws notes as human rows with no reply and groups a quick follow-up from the same person', () => {
    open(true);
    thread.messages = [note('n1', 'one', day(18, 10, 0)), note('n2', 'two', day(18, 10, 2)), note('n3', 'three', day(18, 10, 9))];
    renderList();
    const rows = screen.getAllByTestId('response');
    expect(rows.map((r) => r.getAttribute('data-mode'))).toEqual(['human', 'human', 'human']);
    expect(rows.map((r) => r.getAttribute('data-header'))).toEqual(['yes', 'no', 'yes']);
  });

  it('draws the question where it was asked and says whom the reply answers when a note came between', () => {
    open(true);
    thread.messages = [
      user('u1', 'tell me a joke', day(18, 10, 0)),
      note('n1', 'what about lunch', day(18, 10, 1)),
      reply('a1', 'Knock knock', day(18, 10, 2)),
    ];
    renderList();
    const rows = screen.getAllByTestId('response');
    expect(rows.map((r) => r.textContent)).toEqual(['tell me a joke', 'what about lunch', 'tell me a joke']);
    expect(rows.map((r) => r.getAttribute('data-mode'))).toEqual(['human', 'human', 'reply']);
    expect(rows[2].getAttribute('data-replying-to')).toBe('Alex');
  });
});

describe('solo chat and flag off', () => {
  it.each([
    ['a solo chat', false, true],
    ['a shared chat with the flag off', true, false],
  ])('keeps one block per turn in %s', (_name, collaborative, flag) => {
    open(collaborative, flag);
    thread.messages = [user('u1', 'tell me a joke', day(18, 10, 0)), reply('a1', 'Knock knock', day(18, 10, 1))];
    renderList();
    expect(screen.queryByRole('log')).toBeNull();
    expect(screen.queryByTestId('day-divider')).toBeNull();
    const rows = screen.getAllByTestId('response');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-mode')).toBe('both');
  });

  it('switches to message rows once an unshared chat has a note (tagging a person posts one)', () => {
    open(false);
    thread.messages = [
      user('u1', 'tell me a joke', day(18, 10, 0)),
      reply('a1', 'Knock knock', day(18, 10, 1)),
      note('n1', 'can you check this', day(18, 10, 2), alice),
    ];
    renderList();
    expect(screen.getByRole('log')).toBeTruthy();
    expect(screen.queryByTestId('note-bubble')).toBeNull();
    const modes = screen.getAllByTestId('response').map((r) => r.getAttribute('data-mode'));
    expect(modes).toEqual(['human', 'reply', 'human']);
  });

  it('switches to message rows when someone else wrote a message, even if the access view is not collaborative yet', () => {
    open(false);
    thread.messages = [user('u1', 'hello there', day(18, 10, 0), bob), reply('a1', 'Hi', day(18, 10, 1))];
    renderList();
    expect(screen.getByRole('log')).toBeTruthy();
  });
});
