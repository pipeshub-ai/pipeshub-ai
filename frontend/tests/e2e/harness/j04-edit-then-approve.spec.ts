/**
 * J-04's edit-then-approve on the harness lane's real stack (DP7-MVP-1): a write the agent wants to send waits on its
 * card; the person edits what will be sent in the card's editor and approves the edited call. Only the edited call
 * reaches the scripted SaaS, once, and the card's receipt keeps what was changed after a reload.
 *
 * The recorded J-04 replays (`test_ga26_p0_journeys.py`) hold the email journey itself (Rule-of-Two, sent once,
 * declined never sent); this is its editor on a running stack, with the lane's write tool standing in for the email.
 */
import { createHash, randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { lane, noActiveRun, script, ScriptedModel, ScriptedSaas, sendTurn, type Lane } from './support/lane';

test.describe.configure({ mode: 'serial' });

const TOOL = 'create_ticket';

/** The SaaS's digest of a call's arguments (deployment/e2e/fake_saas: Python's `json.dumps(…, sort_keys=True)`). */
function saasDigest(args: Record<string, string>): string {
  const sorted = Object.keys(args)
    .sort()
    .map((key) => `${JSON.stringify(key)}: ${JSON.stringify(args[key])}`)
    .join(', ');
  return createHash('sha256').update(`{${sorted}}`).digest('hex').slice(0, 16);
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

test('J-04: the person edits the held write in its card and only the edited call is sent, once', async ({ page, apiContext }) => {
  test.setTimeout(300_000);
  await model.reset();
  await saas.reset();
  const t = randomBytes(3).toString('hex');
  const title = `Q3 summary ${t}`;
  const drafted = `Draft body ${t}.`;
  const edited = `Edited body ${t}: send to the leads only.`;
  const done = `Sent the Q3 summary ${t}.`;
  const conversationId = await sendTurn(
    page,
    l.toolAgentKey,
    script(
      `Send the Q3 summary ${t} to the team.`,
      `tool search_tools ${JSON.stringify({ query: TOOL, make_visible: true })}`,
      `tool ${TOOL} ${JSON.stringify({ title, body: drafted })}`,
      `say ${done}`,
    ),
  );
  const card = page.getByTestId('approval-card').last();
  await expect(card).toBeVisible({ timeout: 90_000 });
  await expect(card).toContainText(drafted);
  expect(await saas.effects(), 'nothing is sent while the card waits').toEqual([]);

  await card.getByTestId('approval-edit').click();
  const form = card.getByTestId('approval-edit-form');
  const body = form.getByTestId('approval-edit-field').filter({ hasText: /body/i }).locator('textarea, input');
  await expect(body).toHaveValue(drafted);
  await body.fill(edited);
  await expect(form.getByTestId('approval-edit-field').filter({ hasText: /body/i })).toHaveAttribute('data-changed', 'true');
  await form.getByTestId('approval-edit-approve').click();

  await expect(page.getByText(done, { exact: true })).toBeVisible({ timeout: 90_000 });
  await noActiveRun(apiContext, l.toolAgentKey, conversationId);
  const effects = await saas.effects();
  expect(effects.map((e) => e.tool)).toEqual([TOOL]);
  expect(effects[0]!.argsDigest, 'what ran is the edit').toBe(saasDigest({ title, body: edited }));
  expect(effects[0]!.argsDigest).not.toBe(saasDigest({ title, body: drafted }));

  await page.reload();
  const receipt = page.getByTestId('approval-edited-receipt').last();
  await expect(receipt).toBeVisible({ timeout: 60_000 });
  await expect(receipt).toContainText(edited);
});
