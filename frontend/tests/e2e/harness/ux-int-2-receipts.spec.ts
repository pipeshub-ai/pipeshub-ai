/**
 * UXINT-2/5 (gallery §3, §8, §9) on the harness lane's real stack:
 * - approve one card, deny the next: each folds to one receipt line at its step ("Allowed once by you",
 *   "Denied by you · nothing was sent"), the steps stay open where the person decided, and the receipts survive a
 *   reload in the same order, above the answer;
 * - a message typed while a card waits shows at once, queued, and the card stays; the box says what waits.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, chatInput, denyCall, lane, noActiveRun, script, ScriptedModel, ScriptedSaas, sendTurn, type Lane } from './support/lane';

test.describe.configure({ mode: 'serial' });

const TOOL = 'create_ticket';
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
test.afterEach(async ({ page }) => {
  const stop = page.getByRole('button', { name: 'Stop generating' });
  if (!page.isClosed() && (await stop.isVisible())) await stop.click();
});

const turn = (tag: string) =>
  script(
    `uxintc-r1 two tickets ${tag}`,
    `tool search_tools ${JSON.stringify({ query: TOOL, make_visible: true })}`,
    `tool ${TOOL} ${JSON.stringify({ title: `keep ${tag}`, body: 'UXINT-2' })}`,
    `tool ${TOOL} ${JSON.stringify({ title: `drop ${tag}`, body: 'UXINT-2' })}`,
    'say Decided both.',
  );

test('UXINT-2: approve, then deny; each is one receipt at its step, and both survive a reload', async ({ page, apiContext }) => {
  const tag = randomBytes(4).toString('hex');
  const conversationId = await sendTurn(page, l.toolAgentKey, turn(tag));
  await allowOnceButton(page, TOOL).click({ timeout: 90_000 });
  await expect(page.getByTestId('interaction-receipt').first()).toContainText('Allowed once by you', { timeout: 90_000 });
  await denyCall(page, TOOL);
  await expect(page.getByText('Decided both.', { exact: true })).toBeVisible({ timeout: 90_000 });
  const receipts = page.getByTestId('interaction-receipt');
  await expect(receipts).toHaveText([/Allowed once by you/, /Denied by you · nothing was sent/]);
  await expect(page.getByTestId('approval-card')).toHaveCount(0);
  await noActiveRun(apiContext, l.toolAgentKey, conversationId);

  await page.reload();
  await page.getByTestId('activity-summary').last().click();
  await expect(receipts).toHaveText([/Allowed once by you/, /Denied by you · nothing was sent/], { timeout: 30_000 });
  expect((await saas.effects()).map((e) => e.tool)).toEqual([TOOL]);
});

test('UXINT-5: a message sent while a card waits shows queued; the card stays; the box says what waits', async ({ page }) => {
  const tag = randomBytes(4).toString('hex');
  await sendTurn(page, l.toolAgentKey, turn(tag));
  await expect(allowOnceButton(page, TOOL)).toBeVisible({ timeout: 90_000 });
  await expect(chatInput(page)).toHaveAttribute('placeholder', 'Waiting for you above. Type to steer the run');
  await chatInput(page).fill(`call it renamed ${tag}`);
  await chatInput(page).press('Enter');
  await expect(page.getByTestId('steering-queued')).toContainText(`call it renamed ${tag}`, { timeout: 30_000 });
  await expect(page.getByTestId('steering-queued')).toContainText('after you decide above');
  await expect(allowOnceButton(page, TOOL)).toBeVisible();
});
