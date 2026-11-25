/**
 * chat-interactions-waiting.spec.ts — UXINT-5 (gallery §11, R9):
 * - the run's 120 s hold runs out: the projector says `interaction_waiting {paused: true}`; the header reads "Paused,
 *   waiting for you" instead of a running time, the card stays actionable, and a late approve resumes the run (D5);
 * - someone the conversation is shared with gets only the count: a "waiting for the owner" line and no buttons.
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';
import { evidenceFonts } from './uxint-replay';
import { FakeRunStream, json, MODEL_INFO, mockInFlightConversation, openConversation, type SseFrame } from './durable-conversation.mock';

const SHOTS = process.env.UXINT_SHOTS;
async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: test.info().outputPath(`${name}.png`) });
  if (SHOTS) {
    // Evidence only: the dev server's own overlay is not part of the page, and icons need their font.
    await page.addStyleTag({ content: 'nextjs-portal { display: none !important; }' });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `${SHOTS}/${name}.png` });
  }
}

const ANSWER = 'done s9.';
type Spec = [string, Record<string, unknown>];

function frames(sessionId: string, run: string, owner: boolean): SseFrame[] {
  const card: Spec[] = owner
    ? [['CUSTOM', { name: 'approval_requested', value: { interactionId: 'ix.approve.t2.0', toolCallId: 'c2', toolName: 'e2esaas__create_ticket', displayName: 'E2Esaas · create_ticket', args: JSON.stringify({ title: 'uxintc s9' }), argsSummary: null, reason: { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false }, seq: 20, afterTurns: 2 }, runId: run }]]
    : [];
  const specs: Spec[] = [
    ['RUN_STARTED', { threadId: sessionId, runId: run }],
    ['TEXT_MESSAGE_START', { messageId: 'm1', role: 'assistant', runId: run }],
    ['TEXT_MESSAGE_CONTENT', { messageId: 'm1', delta: 'Filing it.', runId: run }],
    ['TEXT_MESSAGE_END', { messageId: 'm1', runId: run }],
    ['TOOL_CALL_START', { toolCallId: 'c2', toolCallName: 'e2esaas__create_ticket', displayName: 'Create ticket', runId: run }],
    ...card,
    ['CUSTOM', { name: 'interaction_waiting', value: { count: 1, paused: false }, runId: run }],
    ['STATE_SNAPSHOT', { runId: run, snapshot: { step: 'held' } }],
    // The worker's hold ran out (`run.suspended`, reason approval).
    ['CUSTOM', { name: 'interaction_waiting', value: { count: 1, paused: true }, runId: run }],
    ['STATE_SNAPSHOT', { runId: run, snapshot: { step: 'paused' } }],
    ['CUSTOM', { name: 'approval_resolved', value: { interactionId: 'ix.approve.t2.0', decision: 'approved' }, runId: run }],
    ['CUSTOM', { name: 'interaction_waiting', value: { count: 0, paused: true }, runId: run }],
    ['CUSTOM', { name: 'interaction_waiting', value: { count: 0, paused: false }, runId: run }],
    ['TOOL_CALL_END', { toolCallId: 'c2', runId: run }],
    ['TOOL_CALL_RESULT', { toolCallId: 'c2', messageId: 'r-c2', content: 'ok', status: 'completed', runId: run }],
    ['TEXT_MESSAGE_START', { messageId: 'm2', role: 'assistant', runId: run }],
    ['TEXT_MESSAGE_CONTENT', { messageId: 'm2', delta: ANSWER, runId: run }],
    ['TEXT_MESSAGE_END', { messageId: 'm2', runId: run }],
    ['STATE_SNAPSHOT', { runId: run, snapshot: { final: true } }],
    ['RUN_FINISHED', { result: { conversation: stored(sessionId, ANSWER), meta: { requestId: run, duration: 1 } } }],
  ];
  return specs.map(([type, data], i) => ({ id: `j:${i + 1}.1/0.0.0`, type, data }));
}
const stored = (sessionId: string, answer?: string) =>
  buildAguiConversation({ conversationId: sessionId, userMessageId: 'u1', question: 'uxintc-s9 file it', modelInfo: MODEL_INFO, ...(answer ? { answer } : {}) });
const through = (all: SseFrame[], step: string) => all.findIndex((f) => f.type === 'STATE_SNAPSHOT' && (f.data.snapshot as { step?: string }).step === step) + 1;

async function open(page: Page, sessionId: string, runId: string, owner: boolean) {
  const all = frames(sessionId, runId, owner);
  const stream = new FakeRunStream(() => all);
  stream.released = through(all, 'held');
  await mockInFlightConversation(page, {
    sessionId,
    runId,
    stream,
    stored: () => stored(sessionId, stream.finished ? ANSWER : undefined),
    ...(owner ? {} : { access: { isOwner: false, accessLevel: 'READ' } }),
  });
  await page.route(`**/api/v1/conversations/${sessionId}/runs/${runId}/interactions/authority`, (route) => json(route, { approve: owner, deny: owner }));
  await page.route(`**/api/v1/conversations/${sessionId}/runs/${runId}/followups*`, (route) => json(route, { suggestions: [] }));
  const decided: string[] = [];
  await page.route(`**/api/v1/conversations/${sessionId}/runs/${runId}/interactions/*/decision`, (route) => {
    decided.push('ix.approve.t2.0');
    stream.releaseAll();
    return json(route, { receipt: { commandId: 'c' }, interaction: { interactionId: 'ix.approve.t2.0', decision: 'approved' } });
  });
  await evidenceFonts(page);
  await openConversation(page, sessionId);
  return { stream, all, decided };
}

test.describe('Chat — a run paused on its card (UXINT-5, D5)', () => {
  test('the hold runs out: "Paused, waiting for you", the card stays, a late approve resumes', async ({ page }) => {
    const { stream, all, decided } = await open(page, 'conv-uxintc-paused', 'run-uxintc-paused', true);
    await expect(page.locator('[data-interaction-active="true"]')).toHaveCount(1, { timeout: 30_000 });
    await expect(page.getByTestId('interaction-paused')).toHaveCount(0);
    // The page reads the stream again when a served read ends; the paused frame comes with the next read.
    stream.released = through(all, 'paused');
    await expect(page.getByTestId('interaction-paused')).toContainText('Paused, waiting for you', { timeout: 30_000 });
    await expect(page.getByTestId('activity-summary').first()).toContainText('Paused, waiting for you');
    await shot(page, 's9-02-after-hold-expired');
    await page.getByRole('button', { name: /^Allow once: / }).click();
    await expect(page.getByText(ANSWER, { exact: true })).toBeVisible({ timeout: 30_000 });
    expect(decided).toEqual(['ix.approve.t2.0']);
    await expect(page.getByTestId('interaction-paused')).toHaveCount(0);
  });
});

test.describe('Chat — someone the conversation is shared with (UXINT-5, R9)', () => {
  test('sees that the run waits on its owner, and no card or button', async ({ page }) => {
    await open(page, 'conv-uxintc-shared', 'run-uxintc-shared', false);
    await expect(page.getByTestId('interaction-waiting-owner')).toContainText("Waiting for the conversation's owner to decide on a step", { timeout: 30_000 });
    await expect(page.getByTestId('approval-card')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Allow once: |^Deny/ })).toHaveCount(0);
    await shot(page, 'shared-01-waiting-for-owner');
  });
});
