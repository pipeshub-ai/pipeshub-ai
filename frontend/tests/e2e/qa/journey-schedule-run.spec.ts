/**
 * QA-CLASSIC journey J3 (was QA-AUTO's workflow journey, whose workflows left with the new interface): a schedule
 * built in Workspace › Schedules, run now, its chat holding the answer. The person makes a schedule for an agent in
 * the dialog; Run now starts it ("Started." with a link to its conversation); the conversation is the agent's and
 * holds the answer its question got; and the schedule's History lists the run, linking the same conversation. The
 * cron names a day far from today, so only Run now fires it. The schedule is deleted after. Scripted model only.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { adminApi, setLabsFlag } from '../helpers/labs-flag.helper';
import { cancelLeftRuns, lane, noActiveRun, script, trackTurn, type Lane } from '../harness/support/lane';
import { answerText } from './classic';

test.describe.configure({ mode: 'serial' });

const SCHEDULES = '/api/v1/agent-harness/schedules';
const SCHEDULED_RUNS = 'ENABLE_SCHEDULED_RUNS';

let l: Lane;
let schedulesBefore = false;
const made: string[] = [];

test.beforeAll(async () => {
  l = lane();
  const api = await adminApi();
  schedulesBefore = await setLabsFlag(api, SCHEDULED_RUNS, true);
  await api.dispose();
});
test.afterAll(async () => {
  const api = await adminApi();
  await setLabsFlag(api, SCHEDULED_RUNS, schedulesBefore);
  await api.dispose();
});
test.afterEach(async ({ apiContext }) => {
  await cancelLeftRuns(apiContext);
  for (const id of made.splice(0)) await apiContext.delete(`${SCHEDULES}/${id}`);
});

test('QA-J3: a schedule made in the dialog runs now, and its conversation and History hold the run', async ({ page, apiContext }) => {
  const agent = ((await (await apiContext.get(`/api/v1/agents/${l.agentKey}`)).json()) as { agent: { name: string } }).agent;
  const name = `QAJ3 digest ${randomBytes(3).toString('hex')}`;
  const answer = `There are no open P1s for ${name}.`;

  await page.goto('/workspace/schedules/');
  await expect(page.getByText('Your schedules', { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'New schedule' }).click();
  const dialog = page.getByRole('dialog', { name: 'New schedule' });
  await dialog.getByLabel('Name', { exact: true }).fill(name);
  await dialog.getByRole('combobox', { name: 'Agent' }).click();
  await page.getByRole('option', { name: agent.name, exact: true }).click();
  await dialog.getByLabel('What to ask').fill(script('List the open P1 tickets.', `say ${answer}`));
  await dialog.getByLabel('Cron expression').fill('0 9 1 1 *');
  await dialog.getByLabel('Time zone').fill('Europe/Berlin');
  const created = page.waitForResponse((r) => r.url().endsWith(SCHEDULES) && r.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Create' }).click();
  const response = await created;
  expect(response.status(), await response.text()).toBe(201);
  made.push(((await response.json()) as { schedule: { id: string } }).schedule.id);
  await expect(dialog).toBeHidden();

  // Run now: started, with its conversation one click away.
  const row = page.getByRole('group', { name, exact: true });
  await expect(row).toContainText(agent.name);
  await row.getByRole('button', { name: 'Run now' }).click();
  const started = row.getByRole('status').filter({ hasText: 'Started.' });
  await expect(started).toBeVisible({ timeout: 60_000 });
  await started.getByRole('link', { name: 'Open the conversation' }).click();
  await expect(page).toHaveURL(new RegExp(`agentId=${l.agentKey}.*conversationId=|conversationId=.*agentId=${l.agentKey}`), { timeout: 30_000 });
  const conversationId = new URL(page.url()).searchParams.get('conversationId') as string;
  trackTurn(l.agentKey, conversationId);
  await expect(answerText(page, answer)).toBeVisible({ timeout: 120_000 });
  await noActiveRun(apiContext, l.agentKey, conversationId);

  // History: the run, by its kind, linking the same conversation.
  await page.goto('/workspace/schedules/');
  const again = page.getByRole('group', { name, exact: true });
  await again.getByRole('button', { name: 'History' }).click();
  const history = page.getByRole('list', { name: `Runs of ${name}` });
  const firing = history.getByRole('listitem');
  await expect(firing).toHaveCount(1, { timeout: 30_000 });
  await expect(firing).toContainText('Run now');
  await expect(firing.getByRole('link', { name: 'Open the conversation' })).toHaveAttribute('href', new RegExp(`conversationId=${conversationId}`));
});
