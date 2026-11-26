/**
 * chat-approval-batch-order.spec.ts — UXINT-0 (R5): a batch of three held calls, decided out of order.
 *
 * The run is served by page.route() as the worker now behaves: a decision on any card of the batch wakes the hold,
 * so the decided call runs at once and the run keeps holding the others. Deciding the second card first must show it
 * decided while the first and third still offer their buttons; deciding those finishes the run. The worker's half is
 * `backend/python/tests/unit/harness_core/durable/test_hold_any.py`.
 */
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';
import {
  FakeRunStream,
  json,
  MODEL_INFO,
  mockInFlightConversation,
  openConversation,
  type SseFrame,
} from './durable-conversation.mock';

const SESSION_ID = 'conv-e2e-approval-batch-001';
const RUN_ID = 'run-e2e-approval-batch-001';
const QUESTION = 'File three tickets';
const ANSWER = 'All three tickets are filed.';
const CALLS = ['alpha', 'bravo', 'charlie'].map((title, i) => ({
  ix: `ix.approve.t1.${i}`,
  call: `call-${i}`,
  args: JSON.stringify({ title: `UXINT batch ${title}` }),
}));

type Spec = [string, Record<string, unknown>];

const held: Spec[] = [
  ['RUN_STARTED', { threadId: SESSION_ID, runId: RUN_ID }],
  ...CALLS.flatMap(({ call, args }): Spec[] => [
    ['TOOL_CALL_START', { toolCallId: call, toolCallName: 'e2esaas__create_ticket', runId: RUN_ID }],
    ['TOOL_CALL_ARGS', { toolCallId: call, delta: args, runId: RUN_ID }],
  ]),
  ...CALLS.map(({ ix, call, args }): Spec => [
    'CUSTOM',
    {
      name: 'approval_requested',
      // UXINT-1: the projector names the batch (`afterTurns`) and journal order, so the three are one card.
      value: { interactionId: ix, toolCallId: call, toolName: 'e2esaas__create_ticket', args, argsSummary: null, seq: 10 + Number(ix.slice(-1)), afterTurns: 1 },
      runId: RUN_ID,
    },
  ]),
];

const stored = (answer?: string) =>
  buildAguiConversation({
    conversationId: SESSION_ID,
    userMessageId: 'msg-user-batch',
    question: QUESTION,
    modelInfo: MODEL_INFO,
    ...(answer ? { answer } : {}),
  });

/** Finished once every card is decided, not once every frame so far is released. */
class BatchStream extends FakeRunStream {
  constructor(frames: () => SseFrame[], private readonly done: () => boolean) {
    super(frames);
  }

  override get finished(): boolean {
    return this.done();
  }
}

/** Frames follow decisions in the order they are made, as the worker runs each decided call when it wakes. */
class BatchRun {
  readonly decided: string[] = [];
  private readonly tail: Spec[] = [];
  readonly stream: FakeRunStream;

  constructor() {
    this.stream = new BatchStream(
      () => [...held, ...this.tail].map(([type, data], i) => ({ id: `j:${i + 1}.1/0.0.0`, type, data }) satisfies SseFrame),
      () => this.decided.length === CALLS.length,
    );
    this.stream.releaseAll();
  }

  decide(ix: string): void {
    const call = CALLS.find((c) => c.ix === ix);
    if (!call || this.decided.includes(ix)) return;
    this.decided.push(ix);
    this.tail.push(
      ['CUSTOM', { name: 'approval_resolved', value: { interactionId: ix, decision: 'approved' }, runId: RUN_ID }],
      ['TOOL_CALL_END', { toolCallId: call.call, runId: RUN_ID }],
      ['TOOL_CALL_RESULT', { toolCallId: call.call, messageId: `r-${call.call}`, content: 'filed', runId: RUN_ID }],
    );
    if (this.decided.length === CALLS.length) {
      this.tail.push(
        ['TEXT_MESSAGE_START', { messageId: 'msg_2', role: 'assistant', runId: RUN_ID }],
        ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_2', delta: ANSWER, runId: RUN_ID }],
        ['TEXT_MESSAGE_END', { messageId: 'msg_2', runId: RUN_ID }],
        ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { final: true } }],
        ['RUN_FINISHED', { result: { conversation: stored(ANSWER), meta: { requestId: RUN_ID, duration: 1 } } }],
      );
    }
  }
}

test.describe('Chat — a batch of approvals decided out of order (UXINT-0)', () => {
  test('deciding the second of three first settles it at once; the others keep their buttons until decided', async ({
    page,
  }) => {
    const run = new BatchRun();
    await mockInFlightConversation(page, {
      sessionId: SESSION_ID,
      runId: RUN_ID,
      stream: run.stream,
      stored: () => stored(run.stream.finished ? ANSWER : undefined),
    });
    await page.route(`**/api/v1/conversations/${SESSION_ID}/runs/${RUN_ID}/interactions/*/decision`, (route) => {
      const ix = decodeURIComponent(route.request().url().split('/interactions/')[1]?.split('/')[0] ?? '');
      run.decide(ix);
      return json(route, { receipt: { commandId: `c-${ix}` }, interaction: { interactionId: ix, decision: 'approved' } });
    });
    // The run exists only in this mock: its authority read would 404 into an error toast over the cards.
    await page.route(`**/api/v1/conversations/${SESSION_ID}/runs/${RUN_ID}/interactions/authority`, (route) =>
      json(route, { approve: true, deny: true }),
    );
    await openConversation(page, SESSION_ID);

    // UXINT-2: one card for the batch; "Review each" gives each call its own buttons.
    const group = page.getByTestId('approval-group');
    await expect(group).toHaveCount(1, { timeout: 15_000 });
    await group.getByTestId('review-each').click();
    const card = (title: string) => group.getByTestId('approval-group-item').filter({ hasText: `UXINT batch ${title}` });
    const approve = (title: string) => card(title).getByRole('button', { name: /^Allow once/ });

    await approve('bravo').click();
    await expect.poll(() => run.decided).toEqual([CALLS[1]?.ix]);
    await expect(card('bravo')).toHaveAttribute('data-status', 'approved', { timeout: 15_000 });
    await expect(approve('alpha')).toBeVisible();
    await expect(approve('charlie')).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('batch-after-second-of-three.png') });

    await approve('charlie').click();
    await approve('alpha').click();
    await expect(page.getByText(ANSWER, { exact: true })).toBeVisible({ timeout: 15_000 });
    expect(run.decided).toEqual([CALLS[1]?.ix, CALLS[2]?.ix, CALLS[0]?.ix]);
  });
});
