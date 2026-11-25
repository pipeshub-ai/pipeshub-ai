/**
 * chat-approval-card.spec.ts — P2.6c UI-5, UX1 CA-8: a durable run holds a tool call for the user's approval; the
 * card shows the tool and its arguments (or, from the flow guard, where and why), and deciding it posts the
 * decision and lets the run finish.
 *
 * The run is served by page.route() as a conversation the page opens while its run waits on the card
 * (`durable-conversation.mock.ts`); the `setup` project signs in against the harness lane's stack. The same
 * journey on the real stack, with the effect counted at the SaaS, is `tests/e2e/harness/em-approvals.spec.ts`.
 */
import type { Page } from '@playwright/test';
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

const SESSION_ID = 'conv-e2e-approval-001';
const RUN_ID = 'run-e2e-approval-001';
const IX = 'ix.approve.t1.0';
const QUESTION = 'Post the release note to #eng';
const ANSWER = 'Posted to #eng.';
const ARGS = '{"channel":"#eng","text":"v2 is out"}';

type Spec = [string, Record<string, unknown>];

const held = (reason?: Record<string, unknown>): Spec[] => [
  ['RUN_STARTED', { threadId: SESSION_ID, runId: RUN_ID }],
  ['TEXT_MESSAGE_START', { messageId: 'msg_1', role: 'assistant', runId: RUN_ID }],
  ['TEXT_MESSAGE_END', { messageId: 'msg_1', runId: RUN_ID }],
  ['TOOL_CALL_START', { toolCallId: 'call-1', toolCallName: 'slack__post_message', runId: RUN_ID }],
  ['TOOL_CALL_ARGS', { toolCallId: 'call-1', delta: ARGS, runId: RUN_ID }],
  [
    'CUSTOM',
    {
      name: 'approval_requested',
      value: {
        interactionId: IX,
        toolCallId: 'call-1',
        toolName: 'slack__post_message',
        args: ARGS,
        argsSummary: null,
        ...(reason ? { reason } : {}),
      },
      runId: RUN_ID,
    },
  ],
];

const stored = (answer?: string) =>
  buildAguiConversation({
    conversationId: SESSION_ID,
    userMessageId: 'msg-user-approval',
    question: QUESTION,
    modelInfo: MODEL_INFO,
    ...(answer ? { answer } : {}),
  });

const finished: Spec[] = [
  ['CUSTOM', { name: 'approval_resolved', value: { interactionId: IX, decision: 'approved' }, runId: RUN_ID }],
  ['TOOL_CALL_END', { toolCallId: 'call-1', runId: RUN_ID }],
  ['TEXT_MESSAGE_START', { messageId: 'msg_2', role: 'assistant', runId: RUN_ID }],
  ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_2', delta: ANSWER, runId: RUN_ID }],
  ['TEXT_MESSAGE_END', { messageId: 'msg_2', runId: RUN_ID }],
  ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { final: true } }],
  ['RUN_FINISHED', { result: { conversation: stored(ANSWER), meta: { requestId: RUN_ID, duration: 1 } } }],
];

/** UX1: the flow guard held the post after the run read a private record, and says so. */
const FLOW_REASON = {
  rule: 'flow_external_write',
  sensitivity: 'private',
  sources: [{ title: 'Release plan', recordId: 'rec-1' }],
  moreSources: 0,
  destination: '#eng',
  conversationGrant: true,
};

/** The run waits on its card until a decision arrives; then the rest of its frames follow. */
class ApprovalRun {
  readonly decided: unknown[] = [];
  readonly stream: FakeRunStream;

  constructor(private readonly heldFrames: Spec[]) {
    this.stream = new FakeRunStream(() =>
      [...this.heldFrames, ...finished].map(([type, data], index) => ({ id: `j:${index + 1}.1/0.0.0`, type, data }) satisfies SseFrame),
    );
    this.stream.released = heldFrames.length;
  }

  decide(body: unknown): void {
    this.decided.push(body);
    this.stream.releaseAll();
  }
}

async function openHeldRun(page: Page, run: ApprovalRun): Promise<void> {
  await mockInFlightConversation(page, {
    sessionId: SESSION_ID,
    runId: RUN_ID,
    stream: run.stream,
    stored: () => stored(run.stream.finished ? ANSWER : undefined),
  });
  await page.route(`**/api/v1/conversations/${SESSION_ID}/runs/${RUN_ID}/interactions/*/decision`, (route) => {
    run.decide(route.request().postDataJSON());
    return json(route, { receipt: { commandId: 'c1' }, interaction: { interactionId: IX, decision: 'approved' } });
  });
  await openConversation(page, SESSION_ID);
}

test.describe('Chat — approval card on a durable run (P2.6c, UX1)', () => {
  test('UI-5: the held call shows a card; approving posts the decision and the run finishes', async ({ page }) => {
    const run = new ApprovalRun(held());
    await openHeldRun(page, run);

    // UXINT-3: the title is what the call does; what will be sent is labelled values, never raw JSON.
    const card = page.getByRole('region', { name: 'Post message in Slack?' });
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card.getByTestId('approval-preview')).toContainText('#eng');

    const approve = card.getByRole('button', { name: 'Allow once: Post message' });
    await approve.focus();
    await page.keyboard.press('Enter');

    await expect.poll(() => run.decided.length).toBe(1);
    expect(run.decided[0]).toEqual({ decision: 'approved', idempotencyKey: `${IX}:approved` });
    await expect(page.getByText(ANSWER, { exact: true })).toBeVisible({ timeout: 15_000 });
  });

  test('UX1 CA-8: a flow-guard card says where and why; allowing for the conversation posts the scope', async ({ page }) => {
    const run = new ApprovalRun(held(FLOW_REASON));
    await openHeldRun(page, run);

    const card = page.getByRole('region', { name: 'Post message to #eng in Slack?' });
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card.getByTestId('approval-why')).toContainText('read private content');
    await expect(card.getByRole('link', { name: 'Release plan' })).toHaveAttribute('href', '/record/rec-1/');
    // The button names the place it allows (WP-113); its accessible name says what and where.
    const allowConversation = card.getByRole('button', { name: 'Allow #eng for this conversation: Post message' });
    await expect(allowConversation).toHaveText('Allow #eng for this conversation');
    await allowConversation.click();

    await expect.poll(() => run.decided.length).toBe(1);
    expect(run.decided[0]).toEqual({
      decision: 'approved',
      idempotencyKey: `${IX}:approved:conversation`,
      scope: 'conversation',
    });
    await expect(page.getByText(ANSWER, { exact: true })).toBeVisible({ timeout: 15_000 });
  });
});
