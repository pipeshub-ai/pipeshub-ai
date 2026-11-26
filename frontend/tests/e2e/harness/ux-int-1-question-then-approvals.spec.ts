/**
 * UXINT-1 (gallery §1, a recorded 7-wonders order) on the harness lane's real stack: a question asked first, then
 * the approvals its answer's run asks. The question (answered: one receipt line) stays above the approvals, each
 * card sits under its own step, DOM order == journal order, live, after the run and after a reload. UXINT-2: one
 * card is the call to action at a time, and the header counts what waits.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import type { Page } from '@playwright/test';
import { allowOnceButton, lane, noActiveRun, openFinishedActivity, script, ScriptedModel, ScriptedSaas, sendTurn, type Lane, waitingCountText } from './support/lane';

test.describe.configure({ mode: 'serial' });

const TOOL = 'create_ticket';
const ASK = `tool ask_user_question ${JSON.stringify({
  user_intent: 'Pick which wonders to draw',
  questions: [{ question: 'Which set of wonders should I draw?', options: [{ label: 'Ancient seven' }, { label: 'New seven (2007)' }], multiSelect: false }],
})}`;

/** What the reply asks, top to bottom: the question's receipt, then each card's state. */
async function interactionOrder(page: Page): Promise<string[]> {
  return page.getByTestId('interaction').evaluateAll((els) =>
    els
      .map((el) => ({ y: el.getBoundingClientRect().top, label: el.textContent?.includes('You answered') ? 'question' : `approval:${el.getAttribute('data-state')}` }))
      .sort((a, b) => a.y - b.y)
      .map((e) => e.label),
  );
}

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

test('UXINT-1 §1: the question asked first stays above the approvals of its answer’s run, live and after a reload', async ({ page, apiContext }) => {
  const tag = randomBytes(4).toString('hex');
  const conversationId = await sendTurn(
    page,
    l.toolAgentKey,
    script(
      `uxintc-q1 Can we create picture of all 7 wonders and then create pptx ${tag}`,
      ASK,
      `tool search_tools ${JSON.stringify({ query: TOOL, make_visible: true })}`,
      `tool ${TOOL} ${JSON.stringify({ title: `wonder 1 ${tag}`, body: 'UXINT-1' })}`,
      `tool ${TOOL} ${JSON.stringify({ title: `wonder 2 ${tag}`, body: 'UXINT-1' })}`,
      'say Made the wonders deck.',
    ),
  );
  await page.getByRole('radio', { name: 'Ancient seven' }).click({ timeout: 90_000 });
  await page.getByRole('button', { name: /^Submit$/ }).last().click();

  await expect(allowOnceButton(page, TOOL)).toHaveCount(1, { timeout: 90_000 });
  await expect.poll(() => interactionOrder(page)).toEqual(['question', 'approval:active']);
  await expect(page.getByTestId('interaction-waiting-count')).toHaveText(waitingCountText(1));
  await allowOnceButton(page, TOOL).click();

  await expect.poll(() => interactionOrder(page), { timeout: 90_000 }).toEqual(['question', 'approval:done', 'approval:active']);
  await allowOnceButton(page, TOOL).click();
  await expect(page.getByText('Made the wonders deck.', { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect.poll(() => interactionOrder(page)).toEqual(['question', 'approval:done', 'approval:done']);
  await noActiveRun(apiContext, l.toolAgentKey, conversationId);
  // QAFIX-1: the page has settled the run (no Stop button), and the receipts are still there before any reload.
  await expect(page.getByTestId('chat-stop-button')).toHaveCount(0, { timeout: 30_000 });
  await openFinishedActivity(page);
  await expect.poll(() => interactionOrder(page)).toEqual(['question', 'approval:done', 'approval:done']);

  await page.reload();
  await page.getByTestId('activity-summary').last().click();
  await expect.poll(() => interactionOrder(page), { timeout: 30_000 }).toEqual(['question', 'approval:done', 'approval:done']);
  expect((await saas.effects()).map((e) => e.tool)).toEqual([TOOL, TOOL]);
});
