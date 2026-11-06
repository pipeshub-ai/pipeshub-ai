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
import type { AskUserQuestionPayload, MessagePart, PendingAskUserQuestion } from '@/chat/types';

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
  userIntent: 'Your issues are split across two projects, so I need to know which one to search.',
  questions: [{ uuid: 'q-project', question: 'Which project should I look in?', options: [{ id: 'apollo', label: 'Apollo' }, { id: 'zeus', label: 'Zeus' }] }],
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

/** True when `a` comes before `b` in the document. */
const precedes = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
const boxes = () => screen.queryAllByTestId('chat-activity-card');
const questionRows = () => screen.queryAllByTestId('trail-question');
/** Opens the box's summary row, which a saved turn starts folded. */
const openBox = () => screen.getAllByRole('button', { expanded: false })[0].click();

beforeEach(() => {
  installBrowserShims();
});

afterEach(() => {
  cleanup();
  extension.readOnlyFor = undefined;
  useChatStore.setState({ activeSlotId: null, slots: {} });
});

describe('a turn that asked two questions in two runs', () => {
  it('is one box, with each question where it was asked and the answer after the box', () => {
    renderRow({ persistedParts: answeredTurn });
    openBox();

    expect(boxes()).toHaveLength(1);
    expect(screen.queryByTestId('activity-below-question-card')).toBeNull();
    const [first, second] = questionRows();
    const order = [
      screen.getByText('Checking what you asked for'),
      first,
      screen.getByText('Searching Apollo now'),
      second,
      screen.getByText('Read the release notes'),
      screen.getByText('Apollo 5.0 ships in May.'),
    ];
    for (let i = 1; i < order.length; i += 1) expect(precedes(order[i - 1], order[i])).toBe(true);
    expect(boxes()[0].contains(order[order.length - 1])).toBe(false);
  });

  it('gives each question its own explanation and the answer the person gave it', () => {
    renderRow({ persistedParts: answeredTurn });
    openBox();

    const [first, second] = questionRows();
    expect(within(first).getByText('Question')).toBeTruthy();
    expect(within(first).getByText(project.userIntent!)).toBeTruthy();
    expect(within(first).getByText('Which project should I look in?')).toBeTruthy();
    expect(within(first).getByText('Apollo')).toBeTruthy();
    expect(first.textContent).not.toContain(release.userIntent!);
    expect(within(second).getByText(release.userIntent!)).toBeTruthy();
    expect(within(second).getByText('Which release do you mean?')).toBeTruthy();
    expect(within(second).getByText('Skipped')).toBeTruthy();
  });

  it('shows typed text as the answer', () => {
    const typed = { 'q-project': { questionUuid: 'q-project', selectedOptionIds: ['__something_else__'], userInputs: { __something_else__: 'the mobile one' } } };
    renderRow({ persistedParts: [...run1, ask('ask-1', 'completed', { payload: project, answers: typed }), ...run3] });
    openBox();

    expect(within(questionRows()[0]).getByText('the mobile one')).toBeTruthy();
  });

  it('keeps one question call with several questions in one row', () => {
    const both: AskUserQuestionPayload = { ...project, questions: [...project.questions, ...release.questions] };
    renderRow({ persistedParts: [...run1, ask('ask-1', 'completed', { payload: both, answers: { ...pickedApollo, ...skippedRelease } }), ...run3] });
    openBox();

    expect(questionRows()).toHaveLength(1);
    expect(within(questionRows()[0]).getByText('Questions')).toBeTruthy();
    expect(within(questionRows()[0]).getByText('2. Which release do you mean?')).toBeTruthy();
  });

  it('leaves a refused or failed ask out, and the box whole', () => {
    renderRow({ persistedParts: answeredTurn });
    openBox();

    expect(questionRows()).toHaveLength(2);
    expect(boxes()).toHaveLength(1);
    expect(screen.queryByText('Ask User Question')).toBeNull();
    expect(screen.getByText('Used 3 tools')).toBeTruthy();
  });

  it('puts the confidence badge after the box, right above the answer', () => {
    const cited = { citations: {}, sources: { r1: 'c1' }, sourcesOrder: ['r1'], citationsOrder: {} } as never;
    renderRow({ persistedParts: answeredTurn, confidence: 'High', citationMaps: cited });

    const badge = screen.getByText('High').parentElement as HTMLElement;
    expect(boxes()[0].contains(badge)).toBe(false);
    expect(precedes(boxes()[0], badge)).toBe(true);
    expect(badge.closest('.level-slot')?.nextElementSibling?.contains(screen.getByText('Apollo 5.0 ships in May.'))).toBe(true);
  });

  it('reads the same live, right after the answer, as after a reload', () => {
    openCard({ payload: release, answers: skippedRelease, status: 'submitted' });
    const live = renderRow({ answer: '', isStreaming: true, streamingContent: '', streamingParts: answeredTurn.slice(0, -1) });
    const liveRows = questionRows().map((row) => row.textContent);
    live.unmount();
    useChatStore.setState({ activeSlotId: null, slots: {} });

    renderRow({ persistedParts: answeredTurn });
    openBox();

    expect(liveRows).toHaveLength(2);
    expect(questionRows().map((row) => row.textContent)).toEqual(liveRows);
    expect(screen.queryByText('Questions asked:')).toBeNull();
  });
});

describe('a turn waiting on its second question', () => {
  const waitingTurn = [...run1, ask('ask-1', 'completed', { payload: project, answers: pickedApollo }), ...run2, ask('ask-2')];

  it('keeps every earlier step in the box, in view, the answered question among them', () => {
    openCard({ payload: release, answers: {}, status: 'pending' });
    renderRow({ answer: '', persistedParts: waitingTurn });

    const box = boxes()[0];
    // Open as the row mounts (a reload while the card waits): the steps that led to the question stay in view.
    expect(within(box).getByRole('button', { name: 'Waiting for your answer' }).getAttribute('aria-expanded')).toBe('true');
    expect(box.contains(screen.getByText('Checking what you asked for'))).toBe(true);
    expect(box.contains(questionRows()[0])).toBe(true);
    expect(box.contains(screen.getByText('Searching Apollo now'))).toBe(true);
    expect(questionRows()).toHaveLength(1);
  });

  it('says it waits for the person instead of reading as done', () => {
    openCard({ payload: release, answers: {}, status: 'pending' });
    renderRow({ answer: '', persistedParts: waitingTurn });

    expect(screen.getByText('Waiting for your answer')).toBeTruthy();
    expect(screen.queryByText(/^Used \d+ tools?$/)).toBeNull();
  });

  it('names who it waits for in a shared chat the viewer cannot answer', () => {
    extension.readOnlyFor = { name: 'Upg Admin' };
    openCard({ payload: release, answers: {}, status: 'pending' });
    renderRow({ answer: '', persistedParts: waitingTurn });

    expect(within(boxes()[0]).getByText('Waiting for Upg Admin to answer')).toBeTruthy();
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

    expect(screen.queryByText('I could not tell which project you meant.')).toBeNull();
  });

  it('still shows a saved card that no row of the box holds', () => {
    renderRow({ persistedAskUserQuestion: project, persistedAskUserQuestionAnswers: pickedApollo, persistedParts: [...run1, ...run3] });

    const cardIntent = screen.getByText(project.userIntent!);
    expect(boxes()[0].contains(cardIntent)).toBe(false);
    expect(precedes(cardIntent, screen.getByText('Apollo 5.0 ships in May.'))).toBe(true);
  });
});

describe('the answered card heading', () => {
  it('reads Question, the same just after answering as after a reload', () => {
    const { unmount } = renderInTheme(<AskUserQuestionCard payload={project} initialAnswers={pickedApollo} status="submitted" />);
    expect(screen.getByText('Question')).toBeTruthy();
    unmount();

    renderInTheme(<AskUserQuestionCard payload={{ ...project, questions: [...project.questions, ...release.questions] }} initialAnswers={pickedApollo} status="persisted" />);
    expect(screen.getByText('Questions')).toBeTruthy();
  });
});

describe('ChatResponse: the confidence badge on a row with question rows', () => {
  const badge = () => screen.queryByText('High')?.parentElement ?? null;

  it('tops the answer, after the box that holds the questions', () => {
    renderRow({ confidence: 'High', persistedParts: answeredTurn });

    const level = badge() as HTMLElement;
    expect(boxes()[0].contains(level)).toBe(false);
    expect(precedes(boxes()[0], level)).toBe(true);
    expect(level.closest('.level-slot')?.nextElementSibling?.contains(screen.getByText('Apollo 5.0 ships in May.'))).toBe(true);
  });

  it('keeps 24 px above the badge and 8 px to the answer', () => {
    renderRow({ confidence: 'High', persistedParts: answeredTurn });

    const level = badge() as HTMLElement;
    // The badge's place (`.level-slot`) holds the 8 px, so it closes with the place on an answer with no level.
    expect(((level.closest('.level-slot') as HTMLElement).parentElement as HTMLElement).style.marginTop).toBe('var(--answer-lead-gap, var(--space-5))');
    expect((level.parentElement as HTMLElement).style.paddingBottom).toBe('var(--space-2)');
  });

  it('shows no badge while a question waits for an answer: there is no answer yet', () => {
    openCard({ payload: release, answers: {}, status: 'pending' });
    renderRow({ answer: '', confidence: 'High', persistedParts: [...run1, ask('ask-1', 'completed', { payload: project, answers: pickedApollo }), ...run2, ask('ask-2')] });

    expect(badge()).toBeNull();
  });
});
