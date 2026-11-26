/**
 * UXINT-1/2/5 in the answer itself: each card sits under the timeline row that raised it, in the order the run
 * asked (DOM order == journal order), live and saved alike; the question asked first stays above the approvals of
 * its answer's run; one card is active, the next waits; the header counts them. A question passed over by a new
 * message is Skipped; a paused run says so; a shared viewer sees "waiting for the owner"; a steer shows queued.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { AskUserQuestionPayload, MessagePart, PendingApproval } from '@/chat/types';

// MessageActions forks through the app router (main #4225); these tests render no router.
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div role="status">Loading</div> }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/api', async () => ({
  ...(await vi.importActual<object>('@/lib/api')),
  apiClient: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) },
}));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';

const initialChat = useChatStore.getState();
const RUN = 'run-x';
const RUN2 = 'run-y';
const ASK: AskUserQuestionPayload = {
  name: 'ask_user_question',
  questions: [{ uuid: 'q1', question: 'Which set of wonders should I draw?', multiSelect: false, options: [{ id: 'a', label: 'Ancient seven', isUserInput: false }, { id: 'b', label: 'New seven (2007)', isUserInput: false }] }],
};
const tool = (id: string, displayName: string, run = RUN, status: MessagePart['status'] = 'completed'): MessagePart => ({
  type: 'tool_call',
  toolCallId: id,
  toolName: 'e2esaas__create_ticket',
  displayName,
  status,
  runId: run,
});
/** Narration between calls keeps each call its own row (consecutive calls fold into one row). */
const say = (content: string): MessagePart => ({ type: 'text', content, settled: true, runId: RUN });
const approval = (id: string, over: Partial<PendingApproval> = {}): PendingApproval => ({
  interactionId: `ix.approve.${id}`,
  toolCallId: id,
  toolName: 'e2esaas__create_ticket',
  displayName: 'E2Esaas · create_ticket',
  args: JSON.stringify({ title: `ticket ${id}` }),
  argsSummary: null,
  status: 'pending',
  errorCode: null,
  runId: RUN,
  reason: { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false },
  ...over,
});

function slot(patch: Record<string, unknown> = {}) {
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-1');
  store.updateSlot(slotId, { isInitialized: true, threadAgentId: 'agent-1', isOwner: true, ...patch });
  store.setActiveSlot(slotId);
  return slotId;
}

function renderAnswer(props: Partial<React.ComponentProps<typeof ChatResponse>>) {
  return render(
    <Theme>
      <ChatResponse question="Can we create picture of all 7 wonders and then create pptx" answer="" messageId="m1" runId={RUN} isLastMessage {...props} />
    </Theme>,
  );
}

const before = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  useFeatureFlagsStore.setState({ flags: {} });
});
afterEach(() => cleanup());

describe('UXINT-1: a card sits under the row that asked it, in journal order', () => {
  it('UXA-1: live, the card is between its own row and the next one; one is active, the next waits', () => {
    slot({ isStreaming: true, pendingApprovals: [approval('c2', { seq: 20, afterTurns: 2 }), approval('c3', { seq: 30, afterTurns: 3, toolName: 'jira__create_issue' })] });
    renderAnswer({
      isStreaming: true,
      streamingParts: [tool('c1', 'Searched tickets'), say('Filing A.'), tool('c2', 'Ticket A', RUN, 'awaiting_approval'), say('Filing B.'), tool('c3', 'Ticket B', RUN, 'awaiting_approval')],
    });
    const [first, second] = screen.getAllByTestId('interaction');
    expect(before(screen.getByText('Ticket A'), first)).toBe(true);
    expect(before(first, screen.getByText('Ticket B'))).toBe(true);
    expect(before(screen.getByText('Ticket B'), second)).toBe(true);
    expect([first.getAttribute('data-state'), second.getAttribute('data-state')]).toEqual(['active', 'queued']);
    expect(screen.getAllByTestId('approval-card')).toHaveLength(1);
    expect(screen.getByTestId('interaction-waiting-count').textContent).toBe('2 waiting for you');
  });

  it('UXA-2: saved, the decided card is a receipt at the same place; no full card is left behind', () => {
    slot();
    renderAnswer({
      persistedParts: [
        tool('c1', 'Searched tickets'),
        say('Filing A.'),
        tool('c2', 'Ticket A'),
        { type: 'approval', interactionId: 'ix.approve.c2', toolCallId: 'c2', toolName: 'e2esaas__create_ticket', args: '{}', status: 'approved', runId: RUN, seq: 20, afterTurns: 2 },
        say('Filing B.'),
        tool('c3', 'Ticket B'),
      ],
    });
    const receipt = screen.getByTestId('interaction-receipt');
    expect(receipt.textContent).toBe('checkCreate ticket in E2esaas · Allowed once by you');
    expect(before(screen.getByText('Ticket A'), receipt)).toBe(true);
    expect(before(receipt, screen.getByText('Ticket B'))).toBe(true);
    expect(screen.queryByTestId('approval-card')).toBeNull();
  });

  it('UXA-2b: calls that fold into one row (a batch) carry their one card under that row', () => {
    slot({ isStreaming: true, pendingApprovals: [0, 1, 2].map((i) => approval(`t4.${i}`, { seq: 40 + i, afterTurns: 4, toolName: 'web__web_search', displayName: 'Web · web_search' })) });
    renderAnswer({ isStreaming: true, streamingParts: [say('Searching.'), ...[0, 1, 2].map((i) => ({ ...tool(`t4.${i}`, 'Web search', RUN, 'awaiting_approval'), toolName: 'web__web_search' }))] });
    expect(screen.getAllByTestId('interaction')).toHaveLength(1);
    expect(screen.getByRole('region', { name: 'Search the web 3 times?' })).toBeTruthy();
    // WP-71: the header counts the calls waiting, the same number the card's "Allow all 3" decides.
    expect(screen.getByRole('button', { name: /Allow all 3/ })).toBeTruthy();
    expect(screen.getByTestId('interaction-waiting-count').textContent).toBe('3 waiting for you');
  });

  it('UXA-3: the question asked first stays above the approvals its answer’s run asked (the owner’s 7-wonders order)', () => {
    slot({
      isStreaming: true,
      pendingApprovals: [approval('r2c1', { runId: RUN2, seq: 9, afterTurns: 1 })],
      pendingAskUserQuestion: { assistantMessageId: 'm1', payload: ASK, answers: { q1: { questionUuid: 'q1', selectedOptionIds: ['a'], userInputs: {} } }, status: 'submitted' },
    });
    renderAnswer({
      isStreaming: true,
      streamingParts: [
        { type: 'tool_call', toolCallId: 'ask-1', toolName: 'internaltools__ask_user_question', status: 'completed', runId: RUN },
        tool('r2c1', 'Wonder 1', RUN2, 'awaiting_approval'),
      ],
    });
    const [question, card] = screen.getAllByTestId('interaction');
    expect(question.textContent).toBe('checkYou answered: Ancient seven');
    expect(before(question, card)).toBe(true);
    expect(card.getAttribute('data-state')).toBe('active');
  });
});

describe('UXINT-5: waiting states', () => {
  it('UXW-1: a question the person moved past is Skipped, not a live card', () => {
    slot();
    renderAnswer({ isLastMessage: false, persistedAskUserQuestion: ASK, persistedParts: [{ type: 'tool_call', toolCallId: 'ask-1', toolName: 'internaltools__ask_user_question', status: 'completed', runId: RUN }] });
    expect(screen.getByTestId('interaction-receipt').textContent).toBe('removeSkipped (you sent a new message)');
    expect(screen.queryByRole('radio')).toBeNull();
  });

  it('UXW-2: a run paused on its card says so in the header instead of a running time', () => {
    slot({ isStreaming: true, interactionWaiting: { count: 1, paused: true }, pendingApprovals: [approval('c2', { seq: 20, afterTurns: 2 })] });
    renderAnswer({ isStreaming: true, streamingParts: [tool('c2', 'Ticket A', RUN, 'awaiting_approval')], createdAt: new Date(Date.now() - 200_000).toISOString() });
    expect(screen.getByTestId('activity-summary').textContent).toContain('Paused, waiting for you');
    expect(screen.getByTestId('interaction-paused')).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Allow once: / })).toBeTruthy();
  });

  it('UXW-3: a shared viewer sees that the run waits on its owner, and no card', () => {
    slot({ isStreaming: true, isOwner: false, interactionWaiting: { count: 2, paused: false } });
    renderAnswer({ isStreaming: true, streamingParts: [tool('c2', 'Ticket A', RUN, 'awaiting_approval')] });
    expect(screen.getByTestId('interaction-waiting-owner').textContent).toContain("Waiting for the conversation's owner to decide on 2 steps");
    expect(screen.queryAllByRole('button', { name: /Allow|Deny/ })).toEqual([]);
  });

  it('UXW-4: a message sent while a card waits shows queued, until the run reads it', () => {
    slot({ isStreaming: true, queuedSteers: [{ messageSeq: 41, text: 'Call it uxint renamed' }], pendingApprovals: [approval('c2', { seq: 20, afterTurns: 2 })] });
    const parts = [tool('c2', 'Ticket A', RUN, 'awaiting_approval')];
    const view = renderAnswer({ isStreaming: true, streamingParts: parts });
    expect(screen.getByTestId('steering-queued').textContent).toContain('Call it uxint renamed');
    expect(screen.getByTestId('steering-queued').textContent).toContain('after you decide above');
    view.rerender(
      <Theme>
        <ChatResponse question="q" answer="" messageId="m1" runId={RUN} isLastMessage isStreaming streamingParts={[...parts, { type: 'user_input', content: 'Call it uxint renamed', messageSeq: 41, runId: RUN }]} />
      </Theme>,
    );
    expect(screen.queryByTestId('steering-queued')).toBeNull();
    expect(screen.getByTestId('steering-message')).toBeTruthy();
  });

  it('UXW-5: a new run on the slot drops the old run’s waiting state and queued messages', () => {
    const id = slot({ durableRun: { runId: RUN, sessionId: 's', agentId: 'a' }, interactionWaiting: { count: 1, paused: true }, queuedSteers: [{ messageSeq: 1, text: 'x' }] });
    useChatStore.getState().updateSlot(id, { durableRun: { runId: RUN2, sessionId: 's', agentId: 'a' } });
    const after = useChatStore.getState().slots[id];
    expect([after.interactionWaiting, after.queuedSteers]).toEqual([null, []]);
  });
});

