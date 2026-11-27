/**
 * UXINT-0 (gallery §12, scenario s11): two same-shaped turns in one conversation, on the harness lane's real stack.
 *
 * Both runs ask `ix.approve.t2.0`: a loop's interaction ids are unique within a run only. The page used to keep run
 * 1's approved card and drop run 2's request as a duplicate, so run 2 waited on a card nobody could see. Now turn 2
 * shows exactly one card, with buttons, and deciding it lets run 2 finish.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import {
  activeRunId,
  allowOnceButton,
  chatInput,
  lane,
  noActiveRun,
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

const ticketTurn = (title: string, after: string) =>
  script(
    `File a ticket titled ${title}.`,
    `tool search_tools ${JSON.stringify({ query: TOOL, make_visible: true })}`,
    `tool ${TOOL} ${JSON.stringify({ title, body: 'Filed by UXINT-0.' })}`,
    `say ${after}`,
  );

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

test('UXINT-0 s11: a second same-shaped turn shows its own card, with buttons, and no card of turn 1', async ({
  page,
  apiContext,
}) => {
  const tag = nonce();
  const one = `uxint s11 one ${tag}`;
  const two = `uxint s11 two ${tag}`;
  const conversationId = await sendTurn(page, l.toolAgentKey, ticketTurn(one, 'First filed.'));
  await expect(allowOnceButton(page, TOOL)).toBeVisible({ timeout: 90_000 });
  const first = await activeRunId(apiContext, l.toolAgentKey, conversationId);
  await allowOnceButton(page, TOOL).click();
  await expect(page.getByText('First filed.', { exact: true })).toBeVisible({ timeout: 90_000 });
  await noActiveRun(apiContext, l.toolAgentKey, conversationId);

  await chatInput(page).fill(ticketTurn(two, 'Second filed.'));
  await chatInput(page).press('Enter');
  await expect(allowOnceButton(page, TOOL)).toHaveCount(1, { timeout: 90_000 });
  const second = await activeRunId(apiContext, l.toolAgentKey, conversationId);
  expect(second).not.toBe(first);
  const live = page.getByTestId('approval-card').filter({ has: allowOnceButton(page, TOOL) });
  await expect(live).toHaveCount(1);
  await expect(live).toContainText(two);
  await expect(page.getByTestId('approval-card').filter({ hasText: one }).filter({ has: allowOnceButton(page, TOOL) })).toHaveCount(0);

  await allowOnceButton(page, TOOL).click();
  await expect(page.getByText('Second filed.', { exact: true })).toBeVisible({ timeout: 90_000 });
  await noActiveRun(apiContext, l.toolAgentKey, conversationId);
  expect((await saas.effects()).map((e) => e.tool)).toEqual([TOOL, TOOL]);
  const timeline = await runTimeline(apiContext, l.toolAgentKey, conversationId, second);
  expect(timeline.approvals.map((a) => a.status)).toEqual(['approved']);
});
