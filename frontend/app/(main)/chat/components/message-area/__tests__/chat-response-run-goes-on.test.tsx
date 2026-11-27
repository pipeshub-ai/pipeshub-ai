/**
 * The steps box of an answer whose run goes on in its row. After Allow on an approval card the run goes on under
 * the steps it had: the box opens again, as with a new question, and folds at the end; so it does on a page read again
 * while the card waited. Regenerate draws the new run's steps open; an answered question card keeps the box as it was.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useChatStore } from '@/chat/store';
import type { AskUserQuestionPayload, MessagePart } from '../../../types';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../answer-content', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../answer-content')>()),
  AnswerContent: ({ content }: { content: string }) => <div data-testid="answer">{content}</div>,
}));

import { ChatResponse, type ChatResponseProps } from '../chat-response';

const search: MessagePart = { type: 'tool_call', toolCallId: 's1', toolName: 'kb_search', displayName: 'Searched Quokka', status: 'completed' };
const cardCall = (over: Partial<MessagePart> = {}): MessagePart => ({
  type: 'tool_call', toolCallId: 'c1', toolName: 'mcp_jira_create_issue', status: 'awaiting_approval',
  approval: { approvalId: 'ap-1', instanceId: 'i1', serverName: 'Jira', toolName: 'create_issue', toolTitle: 'Create an issue', expiresAt: Date.now() + 900_000, arguments: {} },
  ...over,
} as MessagePart);
const WAITING = 'Waiting for your approval to run create_issue on Jira.';
const stopped: Partial<ChatResponseProps> = { answer: WAITING, persistedParts: [search, cardCall(), { type: 'text', content: WAITING, isFinal: true }] };
const allowed = cardCall({ status: 'completed', approved: true, approvalAnswer: 'allowed' } as Partial<MessagePart>);
const thinking = { id: 's1', status: 'calling_llm', message: 'Thinking…', timestamp: '' } as ChatResponseProps['currentStatusMessage'];
const running = (...more: MessagePart[]): Partial<ChatResponseProps> => ({
  ...stopped, isStreaming: true, streamingContent: '', streamingParts: [search, allowed, ...more], currentStatusMessage: thinking,
});
const done: Partial<ChatResponseProps> = {
  answer: 'Filed as JIRA-7.',
  persistedParts: [search, allowed, { type: 'text', content: 'Filed as JIRA-7.', isFinal: true }],
};

const row = (props: Partial<ChatResponseProps>) => (
  <ChatResponse question="file the login bug" answer="" messageId="a1" rowId="a1" isLastMessage {...props} />
);
function draw(props: Partial<ChatResponseProps>) {
  const view = renderInTheme(row(props));
  return { redraw: (next: Partial<ChatResponseProps>) => view.rerender(<Theme>{row(next)}</Theme>) };
}
const head = () => screen.getByTestId('chat-activity-card').querySelector<HTMLElement>('[role=button][aria-expanded]');
const open = () => head()?.getAttribute('aria-expanded') ?? 'no box to fold';
/** A step drawn where the reader can see it. */
const inView = (text: string) => screen.getAllByText(text).some((el) => el.closest('[aria-hidden="true"]') === null);

beforeEach(() => {
  installBrowserShims();
  useChatStore.setState({ activeSlotId: 'slot-1', slots: { 'slot-1': { isStreaming: false } } as never });
});
afterEach(() => {
  cleanup();
  useChatStore.setState({ activeSlotId: null, slots: {} });
});

describe('the steps box while the run goes on after Allow', () => {
  it('is open, with its steps in view, and folds at the end', () => {
    const { redraw } = draw(stopped);
    const box = head();
    expect(open()).toBe('false');
    redraw(running());
    expect(head()).toBe(box);
    expect(open()).toBe('true');
    expect(inView('Thinking…')).toBe(true);
    const next: MessagePart = { type: 'tool_call', toolCallId: 's2', toolName: 'kb_search', displayName: 'Searched the risks', status: 'running' };
    redraw(running({ type: 'text', content: 'Filed. Now the risks.', settled: true }, next));
    expect(open()).toBe('true');
    expect(inView('Filed. Now the risks.')).toBe(true);
    redraw(done);
    expect(head()).toBe(box);
    expect(open()).toBe('false');
    expect(screen.getByTestId('tool-approval-receipts').textContent).toContain('You allowed Create an issue');
  });

  it('is the same from the run that stopped on the card, live', () => {
    const { redraw } = draw({ isStreaming: true, streamingContent: 'I will file it.', streamingParts: [search] });
    expect(open()).toBe('true');
    redraw(stopped);
    expect(open()).toBe('false');
    redraw(running());
    expect(open()).toBe('true');
    redraw(done);
    expect(open()).toBe('false');
  });

  it('stays folded for a reader who folded it by hand', () => {
    const { redraw } = draw(stopped);
    fireEvent.click(head() as HTMLElement);
    fireEvent.click(head() as HTMLElement);
    expect(open()).toBe('false');
    redraw(running());
    expect(open()).toBe('false');
  });
});

describe('the steps box of the other runs that go on in a row', () => {
  const ask: AskUserQuestionPayload = { name: 'ask_user_question', userIntent: 'Two projects.', questions: [{ uuid: 'q1', question: 'Which project?', options: [{ id: 'apollo', label: 'Apollo' }] }] } as never;
  const askCall = (question?: MessagePart['question']): MessagePart =>
    ({ type: 'tool_call', toolCallId: 'ask-1', toolName: 'internaltools__ask_user_question', status: 'completed', ...(question ? { question } : {}) }) as MessagePart;

  it('a question card: open while it waits and while the answered run goes on, unless folded by hand', () => {
    const waits = { assistantMessageId: 'a1', payload: ask, answers: {}, status: 'pending' } as never;
    useChatStore.setState({ slots: { 'slot-1': { isStreaming: false, pendingAskUserQuestion: waits } } as never });
    const onCard: Partial<ChatResponseProps> = { answer: '', persistedParts: [search, askCall()], persistedAskUserQuestion: ask };
    const answeredRun: Partial<ChatResponseProps> = {
      ...onCard, isStreaming: true, streamingContent: '',
      streamingParts: [search, askCall({ payload: ask, answers: { q1: { questionUuid: 'q1', selectedOptionIds: ['apollo'], userInputs: {} } } }), { type: 'text', content: 'Apollo it is.', settled: true }],
    };
    const { redraw } = draw(onCard);
    expect(open()).toBe('true');
    useChatStore.setState({ slots: { 'slot-1': { isStreaming: true, pendingAskUserQuestion: null } } as never });
    redraw(answeredRun);
    expect(open()).toBe('true');
    cleanup();

    useChatStore.setState({ slots: { 'slot-1': { isStreaming: false, pendingAskUserQuestion: waits } } as never });
    const again = draw(onCard);
    fireEvent.click(head() as HTMLElement);
    expect(open()).toBe('false');
    useChatStore.setState({ slots: { 'slot-1': { isStreaming: true, pendingAskUserQuestion: null } } as never });
    again.redraw(answeredRun);
    expect(open()).toBe('false');
  });

  it("Regenerate: the saved answer's steps go, and the new run's are open until it ends", () => {
    const saved: Partial<ChatResponseProps> = { answer: 'Two.', persistedParts: [search, { type: 'text', content: 'Two.', isFinal: true }] };
    const { redraw } = draw(saved);
    expect(open()).toBe('false');
    redraw({ ...saved, answer: '', isStreaming: true, streamingContent: '', streamingParts: [] });
    expect(screen.queryByTestId('chat-activity-card')).toBeNull();
    redraw({ ...saved, answer: '', isStreaming: true, streamingContent: '', streamingParts: [search, { type: 'text', content: 'Looking again.', settled: true }] });
    expect(open()).toBe('true');
    expect(inView('Looking again.')).toBe(true);
    redraw({ ...saved, answer: 'Three.' });
    expect(open()).toBe('false');
  });
});
