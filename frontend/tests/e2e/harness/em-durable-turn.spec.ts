/**
 * EM-01, EM-01b, EM-13, HX-1 (test plan 04 §3, QA-5): a durable chat turn on the harness lane's real stack.
 *
 * Nothing is mocked. The turn is typed into the agent's chat; the scripted model answers from the turn's
 * directives (`hold` keeps its call open until the spec releases it), so the spec decides when the run is
 * mid-turn without a timer. These replace the mocked F-1..F-4 of chat-durable-stream.spec.ts.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import {
  activeRunId,
  chatInput,
  conversationPath,
  lane,
  noActiveRun,
  recordPostBodies,
  runTimeline,
  script,
  ScriptedModel,
  ScriptedSaas,
  sendTurn,
  untilHolding,
  type Lane,
} from './support/lane';
import { setLabsFlag } from '../helpers/labs-flag.helper';

test.describe.configure({ mode: 'serial' });

const nonce = () => randomBytes(4).toString('hex');
const answerOnly = (page: import('@playwright/test').Page, text: string) => page.getByText(text, { exact: true });

let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;

test.beforeAll(async () => {
  l = lane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
});

test.afterAll(async () => {
  await model.release();
  await model.close();
  await saas.close();
});

test.beforeEach(async () => {
  await model.reset();
  await saas.reset();
});

test.describe('Durable chat turn on the real stack (EM-01, EM-13)', () => {
  test('EM-01: the answer streams, reads the same after a reload, and the run completed with no effect', async ({
    page,
    apiContext,
  }) => {
    const answer = `The durable answer is ${nonce()}.`;
    const conversationId = await sendTurn(page, l.agentKey, script('What is the durable answer?', 'hold', `say ${answer}`));
    await untilHolding(model);
    const runId = await activeRunId(apiContext, l.agentKey, conversationId);
    await model.release();

    await expect(answerOnly(page, answer)).toBeVisible({ timeout: 60_000 });
    await noActiveRun(apiContext, l.agentKey, conversationId);
    await page.reload();
    await expect(answerOnly(page, answer)).toHaveCount(1, { timeout: 30_000 });

    expect((await runTimeline(apiContext, l.agentKey, conversationId, runId)).status).toBe('completed');
    expect(await saas.effects()).toEqual([]);
  });

  test('EM-01b: closing the tab leaves the run going; reopening shows it working, then the answer', async ({
    page,
    context,
    apiContext,
  }) => {
    const question = `Is the run still going ${nonce()}?`;
    const answer = `It kept going ${nonce()}.`;
    const conversationId = await sendTurn(page, l.agentKey, script(question, 'hold', `say ${answer}`));
    await untilHolding(model);
    const runId = await activeRunId(apiContext, l.agentKey, conversationId);
    const url = page.url();
    await page.close();

    const reopened = await context.newPage();
    await reopened.goto(url);
    await expect(reopened.getByText(question).first()).toBeVisible({ timeout: 30_000 });
    expect(await activeRunId(apiContext, l.agentKey, conversationId)).toBe(runId);
    await expect(answerOnly(reopened, answer)).toHaveCount(0);
    await model.release();
    await expect(answerOnly(reopened, answer)).toBeVisible({ timeout: 60_000 });
    // The answer streams before the run's completion commits, as EM-01 waits for.
    await noActiveRun(apiContext, l.agentKey, conversationId);
    expect((await runTimeline(apiContext, l.agentKey, conversationId, runId)).status).toBe('completed');
  });

  test('EM-13: Stop during the model call; the stopped turn reads the same after a reload and the run is cancelled', async ({
    page,
    apiContext,
  }) => {
    const never = `This must never show ${nonce()}.`;
    const conversationId = await sendTurn(page, l.agentKey, script('Write a long report.', 'hold', `say ${never}`));
    await untilHolding(model);
    const runId = await activeRunId(apiContext, l.agentKey, conversationId);
    // WP-71: the Stop goes to the run's own cancel route, not today's loop's `/cancel`.
    const cancels: string[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && /\/cancel$/.test(new URL(r.url()).pathname)) cancels.push(new URL(r.url()).pathname);
    });

    await page.getByRole('button', { name: /stop/i }).first().click();
    await expect.poll(() => cancels, { timeout: 15_000 }).toEqual([`${conversationPath(l.agentKey, conversationId)}/runs/${runId}/cancel`]);
    await expect(page.getByText(/stopped/i).first()).toBeVisible({ timeout: 30_000 });
    await noActiveRun(apiContext, l.agentKey, conversationId);
    expect((await runTimeline(apiContext, l.agentKey, conversationId, runId)).status).toBe('cancelled');

    await model.release();
    await page.reload();
    await expect(page.getByText(/stopped/i).first()).toBeVisible({ timeout: 30_000 });
    await expect(answerOnly(page, never)).toHaveCount(0);
    expect(await model.loopCalls(), 'no model call after the stop').toHaveLength(1);
  });
});

test.describe('With the org’s durable runs off (HX-1, was F-4)', () => {
  let wasOn = true;

  test.beforeAll(async ({ apiContext }) => {
    wasOn = await setLabsFlag(apiContext, 'ENABLE_AGENT_HARNESS', false);
  });

  test.afterAll(async ({ apiContext }) => {
    await setLabsFlag(apiContext, 'ENABLE_AGENT_HARNESS', wasOn);
  });

  test('HX-1: the same agent turn is answered on today’s loop, with no run on the conversation', async ({
    page,
    apiContext,
  }) => {
    const answer = `Answered without a run ${nonce()}.`;
    const conversationId = await sendTurn(page, l.agentKey, script('Answer on the legacy loop.', `say ${answer}`));
    await expect(answerOnly(page, answer)).toBeVisible({ timeout: 60_000 });
    await expect(chatInput(page)).toBeEditable();
    await noActiveRun(apiContext, l.agentKey, conversationId);
    expect(await model.loopCalls()).toHaveLength(1);
  });
});

test.describe('The person chose the classic chat (CTL-7)', () => {
  test('with "New agent harness" off, a new agent conversation is answered on today\'s loop, with no run', async ({
    page,
    apiContext,
  }) => {
    await page.addInitScript(() => window.localStorage.setItem('pipeshub-new-agent-harness', 'false'));
    const bodies = recordPostBodies(page);
    const answer = `Answered on the classic chat ${nonce()}.`;
    const conversationId = await sendTurn(page, l.agentKey, script('Answer on the classic chat.', `say ${answer}`));
    await expect(answerOnly(page, answer)).toBeVisible({ timeout: 60_000 });
    await expect(chatInput(page)).toBeEditable();
    expect(bodies.some((body) => body.includes('"harness":"legacy"')), 'the turn asked for the classic path').toBe(true);
    await noActiveRun(apiContext, l.agentKey, conversationId);
    await expect(page.getByTestId('activity-summary')).toHaveCount(0);
    expect(await model.loopCalls()).toHaveLength(1);
  });
});
