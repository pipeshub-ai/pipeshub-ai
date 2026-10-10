/**
 * Collaboration on a message row: who asked, whose access answered, who may answer an
 * ask_user_question card, and who may regenerate. Solo chats and a flag-off build stay as they were.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const streamMessageForSlot = vi.hoisted(() => vi.fn());
vi.mock('../../../streaming', () => ({
  streamMessageForSlot: (...args: unknown[]) => streamMessageForSlot(...args),
}));
vi.mock('../../../runtime', () => ({
  buildStreamChatRequestForSlot: () => ({ query: 'User selections' }),
}));
vi.mock('@/lib/hooks/use-chat-speech-config', () => ({
  useChatSpeechConfig: () => ({ hasTts: false, isLoading: false }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../response-tabs', () => ({ ResponseTabs: () => null }));
vi.mock('../answer-content', () => ({
  AnswerContent: ({ content }: { content: string }) => <div data-testid="answer">{content}</div>,
}));
vi.mock('../expandable-user-query', () => ({
  ExpandableUserQuery: ({ question }: { question: string }) => <div>{question}</div>,
}));
vi.mock('../../../../knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));
vi.mock('@/knowledge-base/api', () => ({ KnowledgeBaseApi: {} }));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '../../../store';
import { useUserStore } from '@/lib/store/user-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import type { AccessView, MessageAuthor } from '../../../collaboration-types';
import type { AskUserQuestionPayload } from '../../../types';

const alice: MessageAuthor = { userId: 'a', displayName: 'Alice' };
const bob: MessageAuthor = { userId: 'b', displayName: 'Bob' };
const initialChat = useChatStore.getState();

const ACCESS: AccessView = {
  role: 'write', isOwner: false, accessLevel: 'write', canSend: true, canManage: false, canInvite: false, isCollaborative: true,
};

const PAYLOAD: AskUserQuestionPayload = {
  name: 'ask_user_question',
  questions: [{ uuid: 'q1', question: 'Which region?', options: [{ id: 'eu', label: 'EU' }] }],
} as never;

function setup(opts: { flag?: boolean; access?: Partial<AccessView>; me?: string; pendingRequestedBy?: MessageAuthor | null; toolCallMessageId?: string } = {}) {
  const { flag = true, access = {}, me = 'a', pendingRequestedBy } = opts;
  const toolCallMessageId = 'toolCallMessageId' in opts ? opts.toolCallMessageId : 'tc1';
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: flag } });
  useUserStore.setState({ profile: { userId: me } as never });
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.setState({ activeSlotId: slotId });
  useChatStore.getState().updateSlot(slotId, {
    access: { ...ACCESS, ...access },
    ...(pendingRequestedBy !== undefined
      ? {
          pendingAskUserQuestion: {
            assistantMessageId: 'row-1',
            payload: PAYLOAD,
            answers: {},
            status: 'pending',
            requestedBy: pendingRequestedBy,
            toolCallMessageId,
          },
        }
      : {}),
  });
}

function row(props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) {
  return render(
    <Theme>
      <ChatResponse
        question="What is Q3?"
        answer="Revenue was 4."
        messageId="row-1"
        citationMessageRowKey="row-1"
        isLastMessage
        createdAt="2026-09-18T10:00:00.000Z"
        author={bob}
        requestedBy={bob}
        {...props}
      />
    </Theme>,
  );
}

beforeEach(() => {
  streamMessageForSlot.mockReset();
  useChatStore.setState({ ...initialChat, slots: {}, activeSlotId: null });
});
afterEach(cleanup);

describe('attribution (UX-10)', () => {
  it('shows the author chip and the visible "asked by" label in a collaborative chat', () => {
    setup();
    row();

    expect(screen.getByTestId('author-chip')).toHaveProperty('textContent', 'BBob');
    const label = screen.getByTestId('answered-as-label');
    expect(label.textContent).toBe('For Bob · their access');
    expect(label.getAttribute('aria-label')).toBe("Asked by Bob · answered using Bob's access");
  });

  it('says "You" for the viewer\'s own turn and drops the access note', () => {
    setup();
    row({ author: alice, requestedBy: alice });

    expect(screen.getByTestId('author-chip').textContent).toContain('You');
    expect(screen.queryByTestId('answered-as-label')).toBeNull();
  });

  it('names a null author "Former member"', () => {
    setup();
    row({ author: null, requestedBy: null });

    expect(screen.getByTestId('author-chip').textContent).toContain('Former member');
    expect(screen.getByTestId('answered-as-label').textContent).toContain('Former member');
  });

  it('shows neither for the viewer\'s own turn in a solo chat', () => {
    setup({ access: { isCollaborative: false } });
    row({ author: alice, requestedBy: alice });

    expect(screen.queryByTestId('author-chip')).toBeNull();
    expect(screen.queryByTestId('answered-as-label')).toBeNull();
  });

  it('still names someone else\'s turn after everyone else left the chat', () => {
    setup({ access: { isOwner: true, isCollaborative: false } });
    row();

    expect(screen.getByTestId('author-chip').textContent).toContain('Bob');
    const label = screen.getByTestId('answered-as-label');
    expect(label.textContent).toBe('For Bob · their access');
    expect(label.getAttribute('aria-label')).toBe("Asked by Bob · answered using Bob's access");
  });

  it('shows neither with the flag off, even in a shared chat', () => {
    setup({ flag: false });
    row();

    expect(screen.queryByTestId('author-chip')).toBeNull();
    expect(screen.queryByTestId('answered-as-label')).toBeNull();
  });

  it('shows nothing for a row that carries no author (an optimistic row)', () => {
    setup();
    row({ author: undefined, requestedBy: undefined });
    expect(screen.queryByTestId('author-chip')).toBeNull();
    expect(screen.queryByTestId('answered-as-label')).toBeNull();
  });
});

describe('Regenerate (FE-02, M-07)', () => {
  const regenerate = () => screen.queryByRole('button', { name: /regenerate/i }) ?? screen.queryByText('refresh');

  it('is offered to the person who asked', () => {
    setup({ me: 'b' });
    row();

    expect(regenerate()).not.toBeNull();
  });

  it('is hidden from everyone else, the owner included', () => {
    setup({ me: 'a', access: { isOwner: true, role: 'owner', accessLevel: 'owner', canManage: true } });
    row();

    expect(regenerate()).toBeNull();
  });

  it('is hidden from a reader', () => {
    setup({ me: 'b', access: { role: 'read', accessLevel: 'read', canSend: false, isCollaborative: false } });
    row();

    expect(regenerate()).toBeNull();
  });

  it('stays as it was in a solo chat and with the flag off', () => {
    setup({ me: 'a', access: { isCollaborative: false } });
    row();
    expect(regenerate()).not.toBeNull();
    cleanup();

    setup({ me: 'a', flag: false });
    row();
    expect(regenerate()).not.toBeNull();
  });
});

describe('the ask_user_question card (F-1, CL-17)', () => {
  const options = () => screen.getAllByRole('radio') as HTMLButtonElement[];

  it('is interactive for the person it was put to, and answers with resume', () => {
    setup({ me: 'b', pendingRequestedBy: bob });
    row({ answer: '' });

    expect(screen.queryByTestId('ask-card-waiting')).toBeNull();
    fireEvent.click(options()[0]);
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));

    expect(streamMessageForSlot).toHaveBeenCalledTimes(1);
    const [, , request, opts] = streamMessageForSlot.mock.calls[0];
    expect(request).toMatchObject({ resume: { toolCallMessageId: 'tc1' } });
    expect(opts).toEqual({ resumeAskUserQuestion: true });
  });

  it('is read-only for someone else: waiting text, disabled inputs, nothing sent', () => {
    setup({ me: 'a', pendingRequestedBy: bob });
    row({ answer: '' });

    expect(screen.getByTestId('ask-card-waiting').textContent).toBe('hourglass_topWaiting for Bob to answer');
    expect(options().every((o) => o.disabled)).toBe(true);
    expect((screen.getByRole('button', { name: /submit/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /skip/i }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /skip/i }));
    expect(streamMessageForSlot).not.toHaveBeenCalled();
  });

  it('is read-only for everyone when the asker has left', () => {
    setup({ me: 'a', pendingRequestedBy: null });
    row({ answer: '' });

    expect(screen.getByTestId('ask-card-waiting').textContent).toContain('Waiting for Former member to answer');
  });

  it('is read-only for a reader', () => {
    setup({ me: 'b', access: { role: 'read', accessLevel: 'read', canSend: false }, pendingRequestedBy: bob });
    row({ answer: '' });

    expect(screen.getByTestId('ask-card-waiting')).toBeTruthy();
  });

  it('keeps the plain text path in a solo chat: no resume, no waiting text', () => {
    setup({ me: 'a', access: { isCollaborative: false }, pendingRequestedBy: bob, toolCallMessageId: 'tc1' });
    row({ answer: '' });

    expect(screen.queryByTestId('ask-card-waiting')).toBeNull();
    fireEvent.click(options()[0]);
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));
    expect(streamMessageForSlot.mock.calls[0][2]).not.toHaveProperty('resume');
  });

  it('omits resume when the card row is not known, leaving the text binding to the server', () => {
    setup({ me: 'b', pendingRequestedBy: bob, toolCallMessageId: undefined });
    row({ answer: '' });

    fireEvent.click(options()[0]);
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));
    expect(streamMessageForSlot.mock.calls[0][2]).not.toHaveProperty('resume');
  });
});

describe('the guest agent that answered (M2)', () => {
  it('shows the agent avatar and name, with the @handle in the tooltip, and keeps the asked-by line', async () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    setup();
    row({ respondingAgent: { key: 'ag-1', name: 'Joke Buddy', handle: 'joke-buddy' } });

    const header = screen.getByTestId('agent-answer-header');
    expect(header.getAttribute('aria-label')).toBe('Answered by Joke Buddy');
    expect(screen.getByTestId('agent-answer-name').textContent).toBe('Joke Buddy');
    await waitFor(() => expect(screen.getByTestId('agent-answer-avatar').textContent).toBe('J'));
    const label = screen.getByTestId('answered-as-label');
    expect(label.textContent).toBe('For Bob · their access');
    expect(label.getAttribute('aria-label')).toBe("Asked by Bob · answered using Bob's access");

    fireEvent.focus(header);
    fireEvent.pointerMove(header);
    await waitFor(() => expect(screen.getAllByText('@joke-buddy').length).toBeGreaterThan(0));
    vi.unstubAllGlobals();
  });

  it('shows the generic "Agent" when the viewer cannot read the agent (only its key arrives)', () => {
    setup();
    row({ respondingAgent: { key: 'ag-secret' } });

    expect(screen.getByTestId('agent-answer-name').textContent).toBe('Agent');
    expect(screen.getByTestId('agent-answer-header').getAttribute('aria-label')).toBe('Answered by Agent');
    expect(screen.queryByText(/ag-secret/)).toBeNull();
  });

  it('adds nothing when no agent answered, and not on an unanswered question', () => {
    setup();
    row();
    expect(screen.queryByTestId('agent-answer-header')).toBeNull();
    cleanup();
    row({ respondingAgent: { key: 'ag-1', name: 'Joke Buddy' }, unanswered: true });
    expect(screen.queryByTestId('agent-answer-header')).toBeNull();
  });
});
