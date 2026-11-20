/**
 * A turn that asked the person questions is one box: every run's work and each question with its answer, as rows in
 * the order they happened, then the answer under the box.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useChatStore } from '@/chat/store';
import type { MessagePart } from '@/chat/types';

const extension = vi.hoisted(() => ({ readOnlyFor: undefined as { name: string | null } | undefined }));

vi.mock('@/config', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/config')>();
  return {
    ...real,
    chatSlots: {
      ...real.chatSlots,
      useResponseExtension: (input: Parameters<typeof real.chatSlots.useResponseExtension>[0]) => ({
        ...real.chatSlots.useResponseExtension(input),
        ...(extension.readOnlyFor ? { askCardReadOnlyFor: extension.readOnlyFor } : {}),
      }),
    },
  };
});
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';
import { AskUserQuestionCard } from '../ask-user-question-card';

const project: AskUserQuestionPayload = {
  name: 'ask_user_question',
  userIntent: 'Pick the project to search',
  questions: [{ uuid: 'q1', question: 'Which project should I look in?', options: [{ id: 'o1', label: 'Apollo' }] }],
};
const release: AskUserQuestionPayload = {
  name: 'ask_user_question',
  userIntent: 'Apollo ships three releases, so I need the one you mean.',
  questions: [{ uuid: 'q-release', question: 'Which release do you mean?', options: [{ id: 'r41', label: '4.1' }, { id: 'r50', label: '5.0' }] }],
};
const pickedApollo = { 'q-project': { questionUuid: 'q-project', selectedOptionIds: ['apollo'], userInputs: {} } };
const skippedRelease = { 'q-release': { questionUuid: 'q-release', selectedOptionIds: ['__no_preference__'], userInputs: {} } };

const ask = (id: string, status: MessagePart['status'] = 'completed', question?: MessagePart['question']): MessagePart => ({
  type: 'tool_call', toolCallId: id, toolName: 'internaltools__ask_user_question', status, ...(question ? { question } : {}),
});
const narration = (content: string): MessagePart => ({ type: 'text', content, settled: true });
const tool = (id: string, displayName: string): MessagePart => ({ type: 'tool_call', toolCallId: id, toolName: 'jira_search', displayName, status: 'completed' });

// Run 1 asks the project; run 2 is refused one question, fails another, then asks the release; run 3 answers.
const run1 = [narration('Checking what you asked for'), tool('search-1', 'Listed your projects')];
const run2 = [ask('ask-refused', 'blocked'), narration('Searching Apollo now'), tool('search-2', 'Searched Apollo'), ask('ask-failed', 'failed')];
const run3 = [tool('search-3', 'Read the release notes'), { type: 'text', content: 'Apollo 5.0 ships in May.', isFinal: true } as MessagePart];
const answeredTurn = [
  ...run1, ask('ask-1', 'completed', { payload: project, answers: pickedApollo }),
  ...run2, ask('ask-2', 'completed', { payload: release, answers: skippedRelease }),
  ...run3,
];

function renderRow(props: Partial<React.ComponentProps<typeof ChatResponse>> = {}) {
  return renderInTheme(
    <ChatResponse question="When does my release ship?" answer="Apollo 5.0 ships in May." messageId="msg-1" isLastMessage {...props} />,
  );
}

function openCard(pending: Omit<PendingAskUserQuestion, 'assistantMessageId'>) {
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-1');
  store.updateSlot(slotId, { pendingAskUserQuestion: { assistantMessageId: 'msg-1', ...pending } });
  store.setActiveSlot(slotId);
}

// The question sits in the timeline at the step that asked it (UXINT direction C): the open card, or once
// answered its receipt line, is the row's `interaction`.
const shownQuestion = () => screen.getByTestId('interaction');

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  extension.readOnlyFor = undefined;
  useChatStore.setState({ activeSlotId: null, slots: {} });
});

describe('ChatResponse â€” question card order', () => {
  it('shows the work done after an answered question under the card', () => {
    renderRow({
      persistedAskUserQuestion: card as never,
      persistedAskUserQuestionAnswers: answers as never,
      persistedParts: parts(beforeNarration, askCall, afterNarration, afterTool, finalText),
    });

    const before = screen.getByText('Checking what you asked for');
    const question = shownQuestion();
    const after = screen.getByText('Searching Apollo now');
    const answer = screen.getByText('Two open issues.');

    expect(question.textContent).toContain('Apollo');
    expect(precedes(before, question)).toBe(true);
    expect(precedes(question, after)).toBe(true);
    expect(precedes(after, answer)).toBe(true);
  });

  it('says it waits for the person instead of reading as done', () => {
    openCard({ payload: release, answers: {}, status: 'pending' });
    renderRow({ answer: '', persistedParts: waitingTurn });

    const question = shownQuestion();
    expect(precedes(screen.getByText('Checking what you asked for'), question)).toBe(true);
    expect(precedes(question, screen.getByText('Two open issues.'))).toBe(true);
  });

  it('names who it waits for in a shared chat the viewer cannot answer', () => {
    extension.readOnlyFor = { name: 'Upg Admin' };
    openCard({ payload: release, answers: {}, status: 'pending' });
    renderRow({ answer: '', persistedParts: waitingTurn });

    const question = shownQuestion();
    expect(precedes(screen.getByText('Checking what you asked for'), question)).toBe(true);
    expect(precedes(screen.getByText('Searching Apollo now'), question)).toBe(true);
  });

  it('asks only the waiting question, under the box', () => {
    openCard({ payload: release, answers: {}, status: 'pending' });
    renderRow({ answer: '', persistedParts: waitingTurn });

    const cardIntent = screen.getAllByText(release.userIntent!).find((node) => !boxes()[0].contains(node))!;
    expect(precedes(boxes()[0], cardIntent)).toBe(true);
    expect(screen.getByText('Step 1 of 1')).toBeTruthy();
    // The answered question is only in the box: the card never takes it back.
    expect(screen.getAllByText(project.questions[0].question).every((node) => boxes()[0].contains(node))).toBe(true);
    expect(screen.getByRole('button', { name: 'Back' }).hasAttribute('disabled')).toBe(true);
  });

  it('shows no answer under a question still waiting', () => {
    openCard({ payload: release, answers: {}, status: 'pending' });
    renderRow({ answer: 'Text an earlier run saved on this row.', persistedParts: waitingTurn });

    expect(screen.queryByText('Text an earlier run saved on this row.')).toBeNull();
  });
});

describe('the run that stops on a card', () => {
  it('keeps its box open when the saved row takes over from the streamed one', () => {
    openCard({ payload: project, answers: {}, status: 'pending' });
    const row = (props: Partial<React.ComponentProps<typeof ChatResponse>>) => (
      <Theme>
        <ChatResponse question="When does my release ship?" answer="" messageId="msg-1" isLastMessage {...props} />
      </Theme>
    );
    const { rerender } = render(row({ isStreaming: true, streamingContent: '', streamingParts: [...run1, ask('ask-1', 'running')] }));
    // The run ends on the card. For one commit the list still holds the streamed row, ended and with no saved steps, so
    // the box goes; the saved row brings it back.
    rerender(row({ isStreaming: false }));
    rerender(row({ isStreaming: false, persistedParts: [...run1, ask('ask-1')] }));

    expect(screen.getByRole('button', { name: 'Waiting for your answer' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('Checking what you asked for').closest('[aria-hidden="true"]')).toBeNull();
  });

  it('draws its card with the saved row, never under the streamed trail and its status line', () => {
    // The card arrives while the streamed row is still up: it waits for the saved row, so it never moves when that lands.
    openCard({ payload: project, answers: {}, status: 'pending' });
    const row = (props: Partial<React.ComponentProps<typeof ChatResponse>>) => (
      <Theme>
        <ChatResponse question="When does my release ship?" answer="" messageId="msg-1" isLastMessage {...props} />
      </Theme>
    );
    const { rerender } = render(row({ isStreaming: true, streamingContent: '', streamingParts: [...run1, ask('ask-1', 'running')] }));
    expect(screen.queryByText(/Which project should I look in/)).toBeNull();

    rerender(row({ isStreaming: false, persistedParts: [...run1, ask('ask-1')] }));
    expect(screen.getByText(/Which project should I look in/)).toBeTruthy();
    expect(document.querySelector('.generating-shimmer-base')).toBeNull();
  });
});

describe('the run that carries on after an answer', () => {
  it('draws the answered question in the box and no card under it', () => {
    openCard({ payload: project, answers: pickedApollo, status: 'submitted' });
    renderRow({ answer: '', isStreaming: true, streamingContent: '', streamingParts: [...run1, ask('ask-1', 'completed', { payload: project, answers: pickedApollo }), narration('Searching Apollo now')] });

    expect(boxes()).toHaveLength(1);
    expect(questionRows()).toHaveLength(1);
    expect(screen.getAllByText(project.userIntent!)).toHaveLength(1);
    expect(precedes(questionRows()[0], screen.getByText('Searching Apollo now'))).toBe(true);
  });

  it('shows none of the text an earlier run left on the row while it streams', () => {
    openCard({ payload: project, answers: pickedApollo, status: 'submitted' });
    renderRow({
      answer: 'I could not tell which project you meant.',
      isStreaming: true,
      streamingContent: '',
      streamingParts: [...run1, ask('ask-1', 'completed', { payload: project, answers: pickedApollo })],
    });

    const question = shownQuestion();
    const after = screen.getByText('Searching Apollo now');
    expect(precedes(screen.getByText('Checking what you asked for'), question)).toBe(true);
    expect(precedes(question, after)).toBe(true);
  });

  it('still shows a saved card that no row of the box holds', () => {
    renderRow({ persistedAskUserQuestion: project, persistedAskUserQuestionAnswers: pickedApollo, persistedParts: [...run1, ...run3] });

    const cardIntent = screen.getByText(project.userIntent!);
    expect(boxes()[0].contains(cardIntent)).toBe(false);
    expect(precedes(cardIntent, screen.getByText('Apollo 5.0 ships in May.'))).toBe(true);
  });
});

    const question = shownQuestion();
    expect(question.textContent).toContain('Pick the project to search');
    expect(precedes(screen.getByText('Checking what you asked for'), question)).toBe(true);
  });
});
