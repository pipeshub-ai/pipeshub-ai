/**
 * chat-engine-tool-words.spec.ts — FIX-TOOLWORDS (QA-AUTO FU-5) in the classic chat, on the real engine path (the
 * `harness-engine` project on `deployment/e2e/run.sh durable`: the lane's engine, E2E_ENGINE, in a broker sandbox,
 * the scripted model, the scripted SaaS MCP server). Nothing is mocked.
 *
 * The job files a ticket through the SaaS's `create_ticket`, which asks first. Its Activity panel lists that call as
 * one step, the one that waits for the person, named in plain words ("E2Esaas · Create ticket"): no second running
 * step beside it in raw tool words ("Tools mcp e2esaas create ticket"), before or after the answer.
 */
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { request as playwrightRequest } from '@playwright/test';
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, sendTurn } from '../harness/support/lane';
import en from '../../../lib/i18n/locales/en-US.json';

test.describe.configure({ mode: 'serial' });

const AGENT_KEY = process.env.E2E_ENGINE_AGENT_KEY;
const LLM_URL = process.env.E2E_LLM_URL;
const SAAS_URL = process.env.E2E_SAAS_URL;
const CONTROL = process.env.E2E_LLM_CONTROL ?? '';
const TOOL = 'create_ticket';
// The words the product shows, from its own locale (R30ENG-FU-1): the card's held step and the Activity panel's.
const STEP_HELD = en.chat.toolCard.needsApproval;
const PANEL_HELD = en.activityPanel.tool.awaiting;
const ASK_POINTER = en.jobAsk.pointer;
/** The words a person must never read for a step: the gateway's server, an MCP prefix, an id's underscores. */
const RAW = /\bmcp\b|\btools\b|_/i;

let model: APIRequestContext;
let saas: APIRequestContext;
const control = (baseURL: string | undefined) => playwrightRequest.newContext({ baseURL, extraHTTPHeaders: { 'X-E2E-Control': CONTROL } });
const holding = async () => ((await (await model.get('/__control/holding')).json()) as { holding: boolean }).holding;
const effects = async () => ((await (await saas.get('/__control/effects')).json()) as { effects: Array<{ tool: string }> }).effects;

test.beforeAll(async () => {
  test.skip(!AGENT_KEY || !LLM_URL || !SAAS_URL, 'needs the durable engine stack (deployment/e2e/run.sh durable)');
  model = await control(LLM_URL);
  saas = await control(SAAS_URL);
});
test.afterAll(async () => {
  await model?.dispose();
  await saas?.dispose();
});

/** Lets every held engine call go until `until` holds. */
async function releaseUntil(page: Page, until: () => Promise<boolean>, timeoutMs = 240_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await until()) return;
    if (await holding()) await model.post('/__control/release');
    await page.waitForTimeout(1_000);
  }
  expect(await until()).toBe(true);
}

/** The panel's steps naming the ticket call, and every step's name. */
async function ticketSteps(panel: Locator): Promise<{ ticket: Locator; names: string[] }> {
  const names = await panel.getByTestId('activity-tool-name').allInnerTexts();
  return { ticket: panel.getByTestId('activity-tool').filter({ hasText: /create ticket/i }), names };
}

test('FIX-TOOLWORDS: a job call that asks first is one step in the Activity panel and the card, in plain words', async ({ page }) => {
  const ticket = `TW ${randomBytes(3).toString('hex')}`;
  // The SaaS keeps every effect of the stack's earlier specs: only this test's count.
  const before = (await effects()).length;
  await sendTurn(page, AGENT_KEY!, [`File a ticket ${ticket}.`, `tool ${TOOL} ${JSON.stringify({ title: ticket, body: 'From FIX-TOOLWORDS.' })}`, `say Ticket ${ticket} is filed.`].join('\n'));

  const allow = allowOnceButton(page, TOOL);
  await releaseUntil(page, async () => (await allow.count()) > 0);
  const job = page.getByTestId('job-card').first();
  await expect(job).toBeVisible();
  // The card says once where the ask is answered (BGUI-9), and its own steps draw the held call as waiting on that
  // ask, not as a running row (FUQUICK-FU-2; R30ENG-FU-1: also when the ask reached the journal before the step).
  await expect(job.getByTestId('job-ask-pointer')).toContainText(ASK_POINTER, { timeout: 30_000 });
  await job.getByRole('button', { name: /^(\d+ steps?|Steps)$/ }).click();
  const steps = job.getByTestId('child-run-panel');
  await expect(steps.getByText(STEP_HELD).first()).toBeVisible({ timeout: 30_000 });
  await expect(steps.locator('[style*="spin"]')).toHaveCount(0);
  await job.getByTestId('job-card-details').click();
  const panel = page.getByTestId('activity-panel');
  await expect(panel).toBeVisible({ timeout: 30_000 });

  await expect(panel.getByTestId('activity-tool').filter({ hasText: /create ticket/i })).toHaveCount(1, { timeout: 30_000 });
  const waiting = await ticketSteps(panel);
  await expect(waiting.ticket).toContainText(PANEL_HELD);
  for (const name of waiting.names) expect(name, `a step's name: ${name}`).not.toMatch(RAW);
  expect((await effects()).slice(before), 'nothing runs while it waits').toEqual([]);

  await allow.first().click();
  await releaseUntil(page, async () => (await job.getAttribute('data-status')) === 'done');
  expect((await effects()).slice(before).map((e) => e.tool)).toEqual([TOOL]);
  await expect(panel.getByTestId('activity-tool').filter({ hasText: /create ticket/i })).toHaveCount(1, { timeout: 30_000 });
  const done = await ticketSteps(panel);
  await expect(done.ticket).not.toContainText(PANEL_HELD);
  for (const name of done.names) expect(name, `a step's name: ${name}`).not.toMatch(RAW);
});
