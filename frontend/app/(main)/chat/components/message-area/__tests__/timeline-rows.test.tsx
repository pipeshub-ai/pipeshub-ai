/**
 * The Slack-style rows of a collaborative chat: a message row per human message, a reply row for the AI with the
 * responder's name, and the accessible names the list exposes.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: () => ({ query: 'x' }) }));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({
  useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../answer-content', () => ({
  AnswerContent: ({ content }: { content: string }) => <div data-testid="answer">{content}</div>,
}));
vi.mock('../response-tabs/citations/sources-tab', () => ({ SourcesTab: () => <div data-testid="sources-tab" /> }));
vi.mock('../../../../knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '../../../store';
import { useUserStore } from '@/lib/store/user-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useParticipantsStore } from '../../../mentions/participants-store';
import type { AccessView, MessageAuthor } from '../../../collaboration-types';
import type { CitationMaps } from '../response-tabs/citations';

const alice: MessageAuthor = { userId: 'a', displayName: 'Alex Rivera' };
const bob: MessageAuthor = { userId: 'b', displayName: 'Bob Builder' };
const initialChat = useChatStore.getState();
const ACCESS: AccessView = {
  role: 'owner', isOwner: true, accessLevel: 'owner', canSend: true, canManage: true, canInvite: true, isCollaborative: true,
};
const TIME = new Date(2026, 8, 18, 10, 42).toISOString();
const clock = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

function setup(opts: { collaborative?: boolean; threadAgentId?: string; agentName?: string } = {}) {
  const { collaborative = true, threadAgentId, agentName } = opts;
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: true } });
  useUserStore.setState({ profile: { userId: 'a' } as never });
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.setState({ activeSlotId: slotId, agentContextDisplayName: agentName ?? null });
  useChatStore.getState().updateSlot(slotId, {
    access: { ...ACCESS, isCollaborative: collaborative },
    ...(threadAgentId ? { threadAgentId } : {}),
  });
}

function row(props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) {
  return render(
    <Theme>
      <ChatResponse
        question="tell me a joke"
        answer="Why did the chicken cross the road?"
        messageId="m1"
        citationMessageRowKey="m1"
        isLastMessage
        createdAt={TIME}
        answeredAt={TIME}
        author={alice}
        requestedBy={alice}
        {...props}
      />
    </Theme>,
  );
}

beforeEach(() => {
  useChatStore.setState({ ...initialChat, slots: {}, activeSlotId: null });
  useParticipantsStore.getState().reset();
});
afterEach(() => {
  cleanup();
  useFeatureFlagsStore.setState({ flags: null });
});

describe('human message row', () => {
  it('is an article named "Name, time" with the author in bold, the time beside it and the text below', () => {
    setup();
    row({ rowMode: 'human', author: bob });
    const article = screen.getByRole('article');
    expect(article.getAttribute('aria-label')).toBe(`Bob Builder, ${clock(TIME)}`);
    expect(within(article).getByTestId('message-author').textContent).toBe('Bob Builder');
    expect(within(article).getByTestId('message-time').textContent).toBe(clock(TIME));
    expect(within(article).getByTestId('message-text').textContent).toBe('tell me a joke');
    expect(within(article).getByTestId('message-avatar')).toBeTruthy();
  });

  it('draws a note the same way: no NOTE label, no heading, no dashed box', () => {
    setup();
    const { container } = row({ rowMode: 'human', question: 'see you at 3', answer: '', author: bob });
    expect(screen.queryByText(/^note$/i)).toBeNull();
    expect(screen.queryByRole('note')).toBeNull();
    expect(screen.queryByTestId('note-bubble')).toBeNull();
    expect(screen.queryByRole('heading')).toBeNull();
    expect(container.innerHTML).not.toContain('dashed');
    expect(screen.getByTestId('message-text').textContent).toBe('see you at 3');
    expect(screen.queryByTestId('reply-message')).toBeNull();
  });

  it('keeps the viewer\'s own message left-aligned in the same row as everyone else\'s', () => {
    setup();
    row({ rowMode: 'human', author: alice });
    const article = screen.getByRole('article');
    expect(article.getAttribute('aria-label')).toContain('Alex Rivera');
    expect(article.getAttribute('style')).not.toMatch(/row-reverse|flex-end/);
    expect(article.getAttribute('data-side')).toBe('left');
    expect(within(article).queryByTestId('message-body-block')).toBeNull();
  });

  it('collapses the avatar and name for a continued message and offers the time in the gutter', () => {
    setup();
    row({ rowMode: 'human', showHeader: false });
    const article = screen.getByRole('article');
    expect(article.getAttribute('aria-label')).toBe(`Alex Rivera, ${clock(TIME)}`);
    expect(within(article).queryByTestId('message-avatar')).toBeNull();
    expect(within(article).queryByTestId('message-author')).toBeNull();
    expect(within(article).getByTestId('message-time-gutter').textContent).toBe(clock(TIME));
  });

  it('shows mention chips and keeps @you highlighted', () => {
    setup();
    row({ rowMode: 'human', question: 'hey <@user:a> and <@user:b>', answer: '', author: bob });
    const chips = screen.getAllByTestId('mention-chip');
    expect(chips).toHaveLength(2);
  });

  it('copies on request from the row\'s toolbar', () => {
    setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    row({ rowMode: 'human', author: bob });
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('tell me a joke');
  });

  it('puts the full date in the time\'s tooltip content source', () => {
    setup();
    row({ rowMode: 'human' });
    expect(screen.getByTestId('message-time').querySelector('time')?.getAttribute('datetime')).toBe(TIME);
  });
});

describe('reply row', () => {
  it('names the assistant "PipesHub" and holds the answer, with a compact access note in its header', () => {
    setup();
    row({ rowMode: 'reply', author: bob, requestedBy: bob });
    const article = screen.getByRole('article');
    expect(article.getAttribute('aria-label')).toBe(`PipesHub, ${clock(TIME)}`);
    expect(within(article).getByTestId('message-author').textContent).toBe('PipesHub');
    expect(within(article).getByTestId('answer').textContent).toContain('chicken');
    const label = within(article).getByTestId('answered-as-label');
    expect(label.textContent).toBe('For Bob Builder · their access');
    expect(label.getAttribute('aria-label')).toBe("Asked by Bob Builder · answered using Bob Builder's access");
    expect(within(article).getByTestId('message-author').parentElement?.contains(label)).toBe(true);
    expect(within(article).queryByText('tell me a joke')).toBeNull();
  });

  it('is mirrored to the right: header at the end edge, body block spanning to it, text left-aligned inside', () => {
    setup();
    row({ rowMode: 'reply', author: bob, requestedBy: bob, replyingTo: bob });
    const article = screen.getByRole('article');
    expect(article.getAttribute('data-side')).toBe('right');
    expect(article.getAttribute('style')).not.toMatch(/grid-template-columns/);
    const block = within(article).getByTestId('message-body-block');
    expect(block.getAttribute('style')).toMatch(/margin-inline-start: var\(--space-8\)/);
    expect(block.getAttribute('style')).not.toMatch(/(^|[ ;])width:/);
    expect(within(article).getByTestId('message-avatar').parentElement).toBe(within(article).getByTestId('message-author').parentElement);
    expect(block.getAttribute('style')).toMatch(/text-align: start/);
    expect(block.contains(within(article).getByTestId('answer'))).toBe(true);
    expect(block.contains(within(article).getByTestId('answered-as-label'))).toBe(false);
  });

  it('names the chat\'s own agent', () => {
    setup({ threadAgentId: 'agent-1', agentName: 'HR Helper' });
    row({ rowMode: 'reply' });
    expect(screen.getByTestId('message-author').textContent).toBe('HR Helper');
  });

  it('names a guest agent from respondingAgent over the chat\'s own', () => {
    setup({ threadAgentId: 'agent-1', agentName: 'HR Helper' });
    row({ rowMode: 'reply', respondingAgent: { key: 'k1', name: 'Joke Buddy', handle: 'joke' } });
    expect(screen.getByTestId('message-author').textContent).toBe('Joke Buddy');
    expect(screen.getByRole('article').getAttribute('aria-label')).toContain('Joke Buddy');
  });

  it('falls back to a generic name for a guest agent the viewer cannot read', () => {
    setup();
    row({ rowMode: 'reply', respondingAgent: { key: 'k1' } });
    expect(screen.getByTestId('message-author').textContent).toBe('Agent');
  });

  it('says whom it replies to only when asked to', () => {
    setup();
    const { unmount } = row({ rowMode: 'reply' });
    expect(screen.queryByTestId('replying-to')).toBeNull();
    unmount();
    row({ rowMode: 'reply', replyingTo: bob });
    expect(screen.getByTestId('replying-to').textContent).toContain('Replying to Bob Builder');
    expect(screen.getByTestId('message-body-block').contains(screen.getByTestId('replying-to'))).toBe(true);
  });

  it('keeps the answer actions and swaps the big tabs for chips that switch the view', () => {
    setup();
    const citationMaps = {
      citations: {},
      sources: {},
      sourcesOrder: ['r1'],
      citationsOrder: { 1: 'c1' },
    } as CitationMaps;
    row({ rowMode: 'reply', citationMaps });
    const chips = screen.getByTestId('response-chips');
    expect(within(chips).getByRole('button', { name: /answer/i }).getAttribute('aria-pressed')).toBe('true');
    expect(within(chips).getByRole('button', { name: /sources/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /copy/i })).toBeTruthy();
    fireEvent.click(within(chips).getByRole('button', { name: /sources/i }));
    expect(within(screen.getByTestId('response-chips')).getByRole('button', { name: /sources/i }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('sources-tab')).toBeTruthy();
    expect(screen.queryByTestId('answer')).toBeNull();
  });

  it('shows no chip row when there is nothing but the answer', () => {
    setup();
    row({ rowMode: 'reply' });
    expect(screen.queryByTestId('response-chips')).toBeNull();
  });

  it('keeps the streaming placeholder inside the reply row, without a time', () => {
    setup();
    row({ rowMode: 'reply', answer: '', isStreaming: true, answeredAt: undefined, streamingContent: 'Thinking about it' });
    const article = screen.getByRole('article');
    expect(article.getAttribute('aria-label')).toBe('PipesHub');
    expect(within(article).getByTestId('answer').textContent).toContain('Thinking about it');
    expect(within(article).queryByTestId('message-time')).toBeNull();
    expect(article.getAttribute('data-side')).toBe('right');
    expect(within(article).getByTestId('message-body-block').contains(within(article).getByTestId('answer'))).toBe(true);
  });

  it('gives the body block a tinted surface and leaves human rows plain', () => {
    setup();
    row({ rowMode: 'reply' });
    const style = screen.getByTestId('message-body-block').getAttribute('style') ?? '';
    expect(style).toMatch(/background: var\(--olive-2\)/);
    expect(style).toMatch(/border-radius: var\(--radius-4\)/);
    expect(style).toMatch(/padding: var\(--space-2\) var\(--space-3\)/);
    cleanup();
    row({ rowMode: 'human' });
    expect(screen.queryByTestId('message-body-block')).toBeNull();
    expect(screen.getByTestId('human-message').getAttribute('style') ?? '').not.toMatch(/olive-2/);
  });

  it('indents the block by one space-4 on a phone, with the avatar inline in the header', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: true, media: q, addEventListener: () => {}, removeEventListener: () => {},
    }));
    try {
      setup();
      row({ rowMode: 'reply' });
      const article = screen.getByRole('article');
      expect(article.getAttribute('data-side')).toBe('right');
      expect(article.getAttribute('style')).not.toMatch(/grid-template-columns/);
      const block = screen.getByTestId('message-body-block');
      expect(block.getAttribute('style')).toMatch(/margin-inline-start: var\(--space-4\)/);
      expect(block.getAttribute('style')).toMatch(/background: var\(--olive-2\)/);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('reply chrome', () => {
  const confident = (confidence: 'High' | 'Medium') => ({ rowMode: 'reply' as const, confidence });

  it('hides a High confidence chip but keeps a Medium one', () => {
    setup();
    const { unmount } = row(confident('High'));
    expect(screen.queryByText('High')).toBeNull();
    unmount();
    row(confident('Medium'));
    expect(screen.getByText('Medium')).toBeTruthy();
  });

  it('says whom the answer streams for in the header, and nothing when the viewer asked', () => {
    setup();
    const { unmount } = row({ rowMode: 'reply', isStreaming: true, answeredAt: undefined, author: bob, requestedBy: bob });
    expect(screen.getByTestId('answering-line').textContent).toContain('Answering Bob Builder');
    expect(screen.queryByTestId('answered-as-label')).toBeNull();
    unmount();
    row({ rowMode: 'reply', isStreaming: true, answeredAt: undefined });
    expect(screen.queryByTestId('answering-line')).toBeNull();
  });

  it('draws a failed answer as an error and retries the question for the asker only', () => {
    setup();
    const onRetry = vi.fn();
    const { unmount } = row({ rowMode: 'reply', failed: true, answer: 'The model is unavailable', onRetry });
    expect(screen.getByTestId('answer-failed').textContent).toContain('The model is unavailable');
    fireEvent.click(screen.getByTestId('answer-failed-retry'));
    expect(onRetry).toHaveBeenCalledWith('tell me a joke');
    unmount();
    row({ rowMode: 'reply', failed: true, answer: 'The model is unavailable', onRetry, author: bob, requestedBy: bob });
    expect(screen.getByTestId('answer-failed')).toBeTruthy();
    expect(screen.queryByTestId('answer-failed-retry')).toBeNull();
  });
});

describe('solo chats in the timeline', () => {
  it('names you on the left and shows no asked-by line under the reply', () => {
    setup({ collaborative: false });
    row({ rowMode: 'human', author: undefined });
    expect(screen.getByTestId('message-author').textContent).toBe('You');
    cleanup();
    row({ rowMode: 'human', author: alice });
    expect(screen.getByTestId('message-author').textContent).toBe('You');
    cleanup();
    row({ rowMode: 'reply', author: alice, requestedBy: alice });
    expect(screen.queryByTestId('answered-as-label')).toBeNull();
  });
});

describe('solo chats', () => {
  it('draw a question and its answer exactly as before: a heading, the tabs, no rows', () => {
    setup({ collaborative: false });
    row();
    expect(screen.queryByRole('article')).toBeNull();
    expect(screen.getByTestId('user-query-heading').textContent).toContain('tell me a joke');
    expect(screen.queryByTestId('response-chips')).toBeNull();
    expect(screen.queryByTestId('human-message')).toBeNull();
    expect(screen.queryByTestId('reply-message')).toBeNull();
  });
});
