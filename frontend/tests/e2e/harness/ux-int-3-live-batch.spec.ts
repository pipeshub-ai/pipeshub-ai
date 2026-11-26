/**
 * UXINT-2 D2 on the harness lane's real stack (QAFIX-5): one model step that calls the same tool three times under
 * the same rule is one card, "Allow all 3" posts the three decisions, the run does all three calls, and the card
 * folds to one receipt that survives the run's end and a reload. The scripted model's `tools` line makes the batch.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { lane, waitingCountText, noActiveRun, openFinishedActivity, script, ScriptedModel, ScriptedSaas, sendTurn, type Lane } from './support/lane';

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

test('UXINT-2 D2: a live batch of three is one card; Allow all 3 runs all three and leaves one receipt', async ({ page, apiContext }) => {
  const tag = randomBytes(4).toString('hex');
  const tickets = ['one', 'two', 'three'].map((n) => ({ title: `batch ${n} ${tag}`, body: 'UXINT-3' }));
  const conversationId = await sendTurn(
    page,
    l.toolAgentKey,
    script(
      `uxintc-b1 three tickets at once ${tag}`,
      `tool search_tools ${JSON.stringify({ query: TOOL, make_visible: true })}`,
      `tools ${TOOL} ${JSON.stringify(tickets)}`,
      'say Filed all three.',
    ),
  );

  const card = page.getByTestId('interaction').filter({ has: page.getByRole('button', { name: 'Allow all 3' }) });
  await expect(card).toHaveCount(1, { timeout: 90_000 });
  await expect(page.getByTestId('interaction-waiting-count')).toHaveText(waitingCountText(3));
  await card.getByRole('button', { name: 'Allow all 3' }).click();

  await expect(page.getByText('Filed all three.', { exact: true })).toBeVisible({ timeout: 90_000 });
  await noActiveRun(apiContext, l.toolAgentKey, conversationId);
  await expect(page.getByTestId('chat-stop-button')).toHaveCount(0, { timeout: 30_000 });
  await openFinishedActivity(page);
  await expect(page.getByTestId('interaction-receipt')).toHaveText([/Allowed all 3 by you/]);

  await page.reload();
  await openFinishedActivity(page);
  await expect(page.getByTestId('interaction-receipt')).toHaveText([/Allowed all 3 by you/], { timeout: 30_000 });
  expect((await saas.effects()).map((e) => e.tool)).toEqual([TOOL, TOOL, TOOL]);
});
