/**
 * EM-02, EM-03 (test plan 04 §3, QA-5): a write the org's approval policy holds, on the harness lane's real stack.
 *
 * The chat-loop agent has the scripted SaaS's `create_ticket` (an MCP write); the lane's seed sets the org's
 * approval rule for writes to "ask". The scripted model calls the tool, then answers. What is asserted: the card
 * the person sees, what the SaaS executed (its own record of effects), the run's timeline, and the page after a
 * reload. The card may come as the org rule's or the flow guard's: the buttons are found by what they say.
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import {
  activeRunId,
  allowOnceButton,
  denyButton,
  denyCall,
  lane,
  noActiveRun,
  openFinishedActivity,
  runTimeline,
  script,
  ScriptedModel,
  ScriptedSaas,
  sendTurn,
  type Lane,
} from './support/lane';

test.describe.configure({ mode: 'serial' });

const TOOL = 'create_ticket';
const nonce = () => randomBytes(4).toString('hex');

// The loop discloses MCP tools lazily: the scripted model finds the tool first, as a model would.
const ticketTurn = (title: string, after: string) =>
  script(
    `File a ticket titled ${title}.`,
    `tool search_tools ${JSON.stringify({ query: TOOL, make_visible: true })}`,
    `tool ${TOOL} ${JSON.stringify({ title, body: 'Filed by EM-02/EM-03.' })}`,
    `say ${after}`,
  );

/** UXINT-2: a decided card is one receipt line at its step, its decision in `data-status`. */
const receipt = (page: Page) => page.getByTestId('interaction-receipt').last();
const allowOnce = (page: Page) => allowOnceButton(page, TOOL);
const deny = (page: Page) => denyButton(page, TOOL);

let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;

test.beforeAll(async () => {
  l = lane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
});

test.afterAll(async () => {
  await model.close();
  await saas.close();
});

test.beforeEach(async () => {
  await model.reset();
  await saas.reset();
});

// A failed case must not leave its run waiting on a card: the admin queue specs read that queue empty.
test.afterEach(async ({ page }) => {
  const stop = page.getByRole('button', { name: 'Stop generating' });
  if (!page.isClosed() && (await stop.isVisible())) await stop.click();
});

test.describe('A held write on a durable chat turn (EM-02, EM-03)', () => {
  test('EM-02: Allow once runs the write exactly once, also after a reload while the card waits; the card reads approved after a reload', async ({
    page,
    apiContext,
  }) => {
    const title = `EM-02 ${nonce()}`;
    const done = `Ticket ${title} is filed.`;
    const conversationId = await sendTurn(page, l.toolAgentKey, ticketTurn(title, done));

    await expect(allowOnce(page)).toBeVisible({ timeout: 90_000 });
    const runId = await activeRunId(apiContext, l.toolAgentKey, conversationId);
    expect(await saas.effects(), 'nothing runs while the card waits').toEqual([]);

    // IX-APR-PRJ-2: a reload while the card waits shows the one card again, and deciding it still works.
    await page.reload();
    await expect(allowOnce(page)).toBeVisible({ timeout: 30_000 });
    await expect(allowOnce(page)).toHaveCount(1);
    await allowOnce(page).focus();
    await page.keyboard.press('Enter');

    await expect(page.getByText(done, { exact: true })).toBeVisible({ timeout: 90_000 });
    await openFinishedActivity(page);
    await expect(receipt(page)).toHaveAttribute('data-status', 'approved');
    await expect(receipt(page)).toContainText('Allowed once by you');
    await noActiveRun(apiContext, l.toolAgentKey, conversationId);
    expect((await saas.effects()).map((e) => e.tool)).toEqual([TOOL]);
    const timeline = await runTimeline(apiContext, l.toolAgentKey, conversationId, runId);
    expect(timeline.status).toBe('completed');
    expect(timeline.approvals.map((a) => a.status)).toEqual(['approved']);

    await page.reload();
    await expect(page.getByText(done, { exact: true })).toBeVisible({ timeout: 30_000 });
    await openFinishedActivity(page);
    await expect(receipt(page)).toHaveAttribute('data-status', 'approved', { timeout: 30_000 });
    await expect(allowOnce(page)).toHaveCount(0);
    expect(await saas.effects(), 'a reload runs nothing again').toHaveLength(1);
  });

  test('EM-03: Deny sends nothing; the card reads denied, the run ends and no card is offered again', async ({ page, apiContext }) => {
    const title = `EM-03 ${nonce()}`;
    const conversationId = await sendTurn(page, l.toolAgentKey, ticketTurn(title, `Finished the request for ${title}.`));

    await expect(deny(page)).toBeVisible({ timeout: 90_000 });
    const runId = await activeRunId(apiContext, l.toolAgentKey, conversationId);
    await denyCall(page, TOOL);

    await noActiveRun(apiContext, l.toolAgentKey, conversationId);
    await openFinishedActivity(page);
    await expect(receipt(page)).toHaveAttribute('data-status', 'denied', { timeout: 60_000 });
    await expect(deny(page)).toHaveCount(0);
    expect(await saas.effects()).toEqual([]);
    const timeline = await runTimeline(apiContext, l.toolAgentKey, conversationId, runId);
    expect(timeline.approvals.map((a) => a.status)).toEqual(['denied']);

    await page.reload();
    await openFinishedActivity(page);
    await expect(receipt(page)).toHaveAttribute('data-status', 'denied', { timeout: 30_000 });
    expect(await saas.effects()).toEqual([]);
  });
});
