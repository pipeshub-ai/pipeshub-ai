/**
 * CARDFAIL-1 on the question card, and ANSWER-CONTRACT-FU-5. In a manual test the person's answers got 503
 * RUN_CONFIG_UNAVAILABLE while the app restarted, and the card stayed as it was, with no reason and no retry. Now the
 * card keeps the person's choice, says why and retries under the same turn id; a refusal says why and is not retried.
 * FU-5: a question is not an answer, so a row that ended on its card shows no confidence.
 */
import React from 'react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useChatStore } from '@/chat/store';
import { cancelCardSend, useCardSends, type SendAttempt, type SendFailure } from '@/chat/card-send';
import type { MessagePart } from '@/chat/types';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));
vi.mock('../../../runtime', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../runtime')>()),
  buildStreamChatRequestForSlot: vi.fn(() => ({ query: 'resume' })),
}));

import { ChatResponse } from '../chat-response';
import { ApprovalInteraction } from '../interaction-card';
import { approvalKey } from '../../../interactions';
import { streamMessageForSlot } from '../../../streaming';

const card = {
  name: 'ask_user_question',
  userIntent: 'Pick where to look',
  questions: [{ uuid: 'q-1', question: 'Which launch?', options: [{ id: 'orbit', label: 'Orbit GA' }, { id: 'saas', label: 'SAAS beta' }] }],
};
const askCall = { type: 'tool_call', toolCallId: 'ask-1', toolName: 'ask_user_question', status: 'completed' };
const parts = (...p: object[]) => p as unknown as MessagePart[];
const RESTARTING: SendFailure = { retry: true, reason: 'restarting' };

let slotId = '';
const pending = () => useChatStore.getState().slots[slotId]?.pendingAskUserQuestion;

function Row(props: Partial<React.ComponentProps<typeof ChatResponse>>) {
  return <ChatResponse question="Check the launch dates" answer="" messageId="msg-1" isLastMessage {...props} />;
}

/** Each call to the stream answers the next outcome, as `streamMessageForSlot` tells its first frame or failure. */
function streamAnswers(...outcomes: SendAttempt<true>[]) {
  vi.mocked(streamMessageForSlot).mockImplementation(async (_sid, _message, _request, options) => {
    const next = outcomes.shift() ?? { sent: true };
    options?.onSendOutcome?.(next);
  });
}

const openCard = () =>
  useChatStore.getState().updateSlot(slotId, {
    pendingAskUserQuestion: { assistantMessageId: 'msg-1', payload: card as never, answers: {}, status: 'pending' },
  });

async function answerOrbit() {
  fireEvent.click(screen.getByRole('radio', { name: 'Orbit GA' }));
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
  });
}

const runIds = () => vi.mocked(streamMessageForSlot).mock.calls.map((call) => call[3]?.runId);

beforeEach(() => {
  installBrowserShims();
  vi.mocked(streamMessageForSlot).mockReset();
  const store = useChatStore.getState();
  slotId = store.createSlot('conv-1');
  store.setActiveSlot(slotId);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  cancelCardSend('question:msg-1');
  useChatStore.setState({ activeSlotId: null, slots: {}, regenAnswers: null, regenTargetMessageId: null });
});

describe('CARDFAIL-1: a question card whose answer cannot be sent', () => {
  it('QC-1: PipesHub restarting: the card keeps the choice, says so, retries under the same turn id, and lands once', async () => {
    vi.useFakeTimers();
    streamAnswers({ failed: RESTARTING }, { failed: { retry: true, reason: 'offline' } }, { sent: true });
    openCard();
    renderInTheme(<Row persistedParts={parts(askCall)} />);
    await answerOrbit();

    const notice = screen.getByTestId('card-send-notice');
    expect(notice.getAttribute('data-phase')).toBe('retrying');
    expect(notice.textContent).toBe("Couldn't send your answer: PipesHub is restarting. Trying again…");
    expect(notice.getAttribute('role')).toBe('status');
    // The person's choice stays on the open card while it is resent; nothing can be changed or sent twice.
    expect(screen.getByRole('radio', { name: 'Orbit GA' }).getAttribute('aria-checked')).toBe('true');
    expect((screen.getByRole('radio', { name: 'SAAS beta' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Submit' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Skip' }) as HTMLButtonElement).disabled).toBe(true);
    expect(pending()?.answers['q-1']?.selectedOptionIds).toEqual(['orbit']);
    expect(pending()?.status).toBe('submitted');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_300);
    });
    expect(screen.getByTestId('card-send-notice').textContent).toBe("Couldn't send your answer: PipesHub can't be reached. Trying again…");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_600);
    });
    expect(screen.queryByTestId('card-send-notice')).toBeNull();
    expect(streamMessageForSlot).toHaveBeenCalledTimes(3);
    const ids = runIds();
    expect(ids[0]).toEqual(expect.any(String));
    expect(new Set(ids).size).toBe(1);
  });

  it('QC-2: still restarting after the bounded retries: the card offers Try again, which sends the same answer', async () => {
    vi.useFakeTimers();
    streamAnswers(...Array.from({ length: 9 }, () => ({ failed: RESTARTING })), { sent: true });
    openCard();
    renderInTheme(<Row persistedParts={parts(askCall)} />);
    await answerOrbit();
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    const notice = screen.getByTestId('card-send-notice');
    expect(notice.getAttribute('data-phase')).toBe('stopped');
    expect(notice.textContent).toContain("Couldn't send your answer: PipesHub is restarting.");
    expect(streamMessageForSlot).toHaveBeenCalledTimes(9);
    expect(pending()?.status).toBe('pending');
    expect(pending()?.answers['q-1']?.selectedOptionIds).toEqual(['orbit']);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    });
    expect(streamMessageForSlot).toHaveBeenCalledTimes(10);
    expect(new Set(runIds()).size).toBe(1);
    expect(screen.queryByTestId('card-send-notice')).toBeNull();
  });

  it('QC-3: a refusal with a reason says why, is not retried, and the card can be answered again', async () => {
    vi.useFakeTimers();
    streamAnswers({ failed: { retry: false, code: 'HTTP_AGENT_HARNESS_RUN_ACTIVE' } });
    openCard();
    renderInTheme(<Row persistedParts={parts(askCall)} />);
    await answerOrbit();
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    const notice = screen.getByTestId('card-send-notice');
    expect(notice.getAttribute('data-phase')).toBe('refused');
    expect(notice.getAttribute('role')).toBe('alert');
    expect(notice.textContent).toMatch(/finish|stop/i);
    expect(streamMessageForSlot).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Submit' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('QC-4: answering twice sends once', async () => {
    vi.mocked(streamMessageForSlot).mockImplementation(() => new Promise(() => undefined));
    openCard();
    renderInTheme(<Row persistedParts={parts(askCall)} />);
    await answerOrbit();
    expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull();
    expect(streamMessageForSlot).toHaveBeenCalledTimes(1);
  });
});

describe('CARDFAIL-1: every approval kind draws the same notice', () => {
  const approval = {
    interactionId: 'ix.approve.t1.0',
    runId: 'run-1',
    toolCallId: 'call-1',
    toolName: 'jira__create_issue',
    args: '{}',
    argsSummary: null,
    status: 'sending',
    errorCode: null,
  } as never;
  const group = { key: 'g', items: [approval] };
  const props = { onDecide: vi.fn(), onDecideAll: vi.fn(), conversationChoice: false, paused: false };

  it('AC-1: retrying, then given up with Try again, under the card that waits', () => {
    const key = approvalKey(approval);
    useCardSends.getState().put(key, { phase: 'retrying', reason: 'restarting' });
    const view = renderInTheme(<ApprovalInteraction group={group as never} state="active" {...props} />);
    expect(screen.getByTestId('card-send-notice').textContent).toBe("Couldn't send your answer: PipesHub is restarting. Trying again…");
    const retry = vi.fn();
    act(() => useCardSends.getState().put(key, { phase: 'stopped', reason: 'server', retry }));
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('card-send-notice').textContent).toContain("Couldn't send your answer: PipesHub ran into a problem.");
    view.unmount();
    useCardSends.getState().put(key, null);
  });
});

describe('ANSWER-CONTRACT-FU-5: no confidence on a question', () => {
  it('FU5-1: the pre-run card waiting for an answer shows no "Medium"', () => {
    openCard();
    renderInTheme(<Row persistedParts={parts(askCall)} persistedAskUserQuestion={card as never} confidence="Medium" />);
    expect(screen.getByTestId('interaction')).toBeTruthy();
    expect(screen.queryByText('Medium')).toBeNull();
  });

  it('FU5-2: a card the person moved past (a new message) is still a question: no confidence', () => {
    renderInTheme(<Row isLastMessage={false} persistedParts={parts(askCall)} persistedAskUserQuestion={card as never} confidence="Medium" />);
    expect(screen.queryByText('Medium')).toBeNull();
  });

  it("FU5-3: an answered card's row shows its answer's confidence, and a row with no card shows its own", () => {
    const answered = { 'q-1': { questionUuid: 'q-1', selectedOptionIds: ['orbit'], userInputs: {} } };
    const view = renderInTheme(
      <Row
        answer="The Orbit GA dates are ready."
        persistedParts={parts(askCall, { type: 'text', content: 'The Orbit GA dates are ready.', isFinal: true })}
        persistedAskUserQuestion={card as never}
        persistedAskUserQuestionAnswers={answered as never}
        confidence="High"
      />,
    );
    expect(screen.getByText('High')).toBeTruthy();
    view.unmount();
    renderInTheme(<Row answer="Three closed issues." confidence="Medium" />);
    expect(screen.getByText('Medium')).toBeTruthy();
  });
});
