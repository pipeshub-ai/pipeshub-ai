/**
 * ui3-trigger-settings.spec.ts — WP-24 schedule triggers in the real UI
 * (docs/agent-harness-v2/ui-cases/wp24-wp25-settings.json lists each case, its steps and its pictures).
 *
 * Runs on a stack built from this branch (the human-test stack, or deployment/e2e/run.sh harness): Labs
 * `ENABLE_SCHEDULED_RUNS` is turned on for the spec and put back afterwards, schedules are
 * created through the dialog against real Node (the notice channels too: the stack has no Slack bot, so Slack
 * is greyed).
 * One answer is served by the page's route because its states cannot be produced on demand: a schedule's
 * history (an overlap skip, a firing waiting for the last run, a slot caught up after an outage).
 *
 * With UI3_SHOTS set to a directory, each case saves its pictures there: desktop (1280x900) and phone
 * (375x812), light and dark, as `<case>-<desktop|phone>-<light|dark>.png`.
 */
import { mkdirSync } from 'fs';
import type { APIRequestContext, Locator, Page, Route } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { adminApi, setLabsFlag } from '../helpers/labs-flag.helper';

test.describe.configure({ mode: 'serial' });

const SCHEDULES = '/api/v1/agent-harness/schedules';
const FLAGS = ['ENABLE_SCHEDULED_RUNS'] as const;
const SHOTS = process.env.UI3_SHOTS ?? '';
const RUN = Date.now().toString(36);

const VIEWPORTS = [
  { name: 'desktop', width: 1280, height: 900 },
  { name: 'phone', width: 375, height: 812 },
] as const;

/** The case's four pictures; `scroll` is brought into view first (a dialog's lower half on a phone). */
async function shoot(page: Page, name: string, scroll?: Locator): Promise<void> {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    for (const scheme of ['light', 'dark'] as const) {
      // The theme follows the system preference unless the person picked one (the default).
      await page.emulateMedia({ colorScheme: scheme });
      await expect(page.locator('html')).toHaveClass(new RegExp(`\\b${scheme}\\b`));
      if (scroll) await scroll.scrollIntoViewIfNeeded();
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${SHOTS}/${name}-${viewport.name}-${scheme}.png` });
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ colorScheme: 'light' });
}

async function mySchedules(api: APIRequestContext): Promise<Array<{ id: string; name: string }>> {
  const res = await api.get(`${SCHEDULES}?scope=mine`);
  expect(res.ok()).toBe(true);
  return ((await res.json()) as { schedules: Array<{ id: string; name: string }> }).schedules;
}

const scheduleName = (tag: string) => `UI3 ${tag} ${RUN}`;

async function openForm(page: Page): Promise<Locator> {
  await page.goto('/workspace/schedules/');
  await expect(page.getByText('Your schedules', { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'New schedule' }).click();
  const dialog = page.getByRole('dialog', { name: 'New schedule' });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function fillCron(dialog: Locator, name: string): Promise<void> {
  await dialog.getByLabel('Name', { exact: true }).fill(name);
  await dialog.getByLabel('What to ask').fill('Summarise what changed this week.');
  await dialog.getByLabel('Cron expression').fill('0 9 * * 1');
  await dialog.getByLabel('Time zone').fill('Europe/Berlin');
}

const notifyGroup = (dialog: Locator) => dialog.getByRole('group', { name: 'Tell me when a run ends' });
const channel = (dialog: Locator, name: string) => notifyGroup(dialog).getByRole('checkbox', { name, exact: true });

async function createFrom(page: Page, dialog: Locator): Promise<{ sent: Record<string, unknown>; body: Record<string, unknown> }> {
  const created = page.waitForResponse((r) => r.url().endsWith(SCHEDULES) && r.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Create' }).click();
  const answer = await created;
  expect(answer.status(), await answer.text()).toBe(201);
  return {
    sent: answer.request().postDataJSON() as Record<string, unknown>,
    body: (await answer.json()) as Record<string, unknown>,
  };
}

// @disposable (round 15): it turns a Labs flag on and makes schedules while it runs, so it never runs on a stack
// people use; every lane stack runs it.
test.describe('WP-24 / WP-25 settings UI (UI-3)', { tag: '@disposable' }, () => {
  const flagsBefore: Record<string, boolean> = {};

  test.beforeAll(async () => {
    const api = await adminApi();
    for (const flag of FLAGS) flagsBefore[flag] = await setLabsFlag(api, flag, true);
    await api.dispose();
  });

  test.afterAll(async () => {
    const api = await adminApi();
    for (const left of (await mySchedules(api)).filter((s) => s.name.endsWith(RUN))) {
      await api.delete(`${SCHEDULES}/${left.id}`);
    }
    for (const flag of FLAGS) await setLabsFlag(api, flag, flagsBefore[flag] ?? false);
    await api.dispose();
  });

  test('UI3-SCH-1: the schedule form defaults: skip missed runs, skip while the last runs, tell in PipesHub; Slack greyed for the assistant', async ({ page }) => {
    const dialog = await openForm(page);
    const catchUp = dialog.getByRole('radiogroup', { name: 'If PipesHub was down' });
    await expect(catchUp.getByRole('radio', { name: /Skip the runs it missed/ })).toHaveAttribute('aria-checked', 'true');
    const overlap = dialog.getByRole('radiogroup', { name: 'If the last run is still going' });
    await expect(overlap.getByRole('radio', { name: /Skip this run/ })).toHaveAttribute('aria-checked', 'true');
    await expect(channel(dialog, 'In PipesHub')).toHaveAttribute('aria-checked', 'true');
    await expect(channel(dialog, 'Email')).toBeEnabled({ timeout: 30_000 });
    await expect(channel(dialog, 'Email')).toHaveAttribute('aria-checked', 'false');
    await expect(channel(dialog, 'Slack')).toBeDisabled();
    await expect(notifyGroup(dialog)).toContainText('The assistant has no Slack bot.');
    await shoot(page, 'ui3-sch-1-form-defaults');
    await shoot(page, 'ui3-sch-1-form-defaults-lower', notifyGroup(dialog));
  });

  test('UI3-SCH-2: each "If PipesHub was down" option can be picked and says what it does', async ({ page }) => {
    const dialog = await openForm(page);
    const catchUp = dialog.getByRole('radiogroup', { name: 'If PipesHub was down' });
    for (const [tag, label] of [
      ['skip', /Skip the runs it missed/],
      ['latest', /Run once for the latest missed time/],
      ['all', /Run once for each missed time/],
    ] as const) {
      await catchUp.getByRole('radio', { name: label }).click();
      await expect(catchUp.getByRole('radio', { name: label })).toHaveAttribute('aria-checked', 'true');
      if (tag === 'all') await expect(catchUp).toContainText('Up to 24, oldest first, one after another.');
      await shoot(page, `ui3-sch-2-catch-up-${tag}`, catchUp);
    }
  });

  test('UI3-SCH-3: each "If the last run is still going" option can be picked and says what it does', async ({ page }) => {
    const dialog = await openForm(page);
    const overlap = dialog.getByRole('radiogroup', { name: 'If the last run is still going' });
    for (const [tag, label] of [
      ['skip', /Skip this run/],
      ['queue', /Wait for it to finish, then run/],
      ['allow', /Run both at the same time/],
    ] as const) {
      await overlap.getByRole('radio', { name: label }).click();
      await expect(overlap.getByRole('radio', { name: label })).toHaveAttribute('aria-checked', 'true');
      if (tag === 'queue') await expect(overlap).toContainText('One run can wait.');
      await shoot(page, `ui3-sch-3-overlap-${tag}`, overlap);
    }
  });

  test('UI3-SCH-4: an agent without a Slack bot greys Slack with the reason; the chosen policies are what Node stores', async ({ page, apiContext }) => {
    const dialog = await openForm(page);
    const name = scheduleName('policies');
    await fillCron(dialog, name);
    await dialog.getByRole('combobox', { name: 'Agent' }).click();
    const agents = page.getByRole('option').filter({ hasNotText: 'Assistant' });
    await expect(agents.first(), 'the stack has a seeded agent').toBeVisible({ timeout: 30_000 });
    const noBot = page.waitForResponse((r) => r.url().includes(`${SCHEDULES}/notify-channels?agentKey=`));
    await agents.first().click();
    expect((await noBot).ok()).toBe(true);
    await expect(channel(dialog, 'Slack')).toBeDisabled();
    await expect(notifyGroup(dialog)).toContainText('This agent has no Slack bot. Add one in Slack settings to get messages there.');
    await dialog.getByRole('radio', { name: /Run once for each missed time/ }).click();
    await dialog.getByRole('radio', { name: /Wait for it to finish, then run/ }).click();
    await channel(dialog, 'Email').click();
    await shoot(page, 'ui3-sch-4-slack-greyed', notifyGroup(dialog));
    const { sent, body } = await createFrom(page, dialog);
    expect(sent).toMatchObject({ trigger: { kind: 'cron', catchUp: 'run_all' }, overlap: 'queue', notify: ['in_app', 'email'] });
    expect((body.schedule as Record<string, unknown>)).toMatchObject({ overlap: 'queue', notify: ['in_app', 'email'] });
    const stored = (await mySchedules(apiContext)).find((s) => s.name === name);
    expect(stored).toBeTruthy();
  });

  test('UI3-SCH-5: a webhook is signed by default; its key is shown once with a signing example', async ({ page }) => {
    const dialog = await openForm(page);
    await dialog.getByLabel('Name', { exact: true }).fill(scheduleName('signed hook'));
    await dialog.getByLabel('What to ask').fill('Triage the payload.');
    await dialog.getByRole('radio', { name: 'When a webhook is called' }).click();
    await expect(dialog.getByRole('checkbox', { name: /The caller can't sign/ })).toHaveAttribute('aria-checked', 'false');
    const { sent, body } = await createFrom(page, dialog);
    expect(sent.trigger).toEqual({ kind: 'webhook', auth: 'signature' });
    expect(typeof body.webhookSecret).toBe('string');
    expect(body.webhookToken).toBeUndefined();
    const secret = page.getByRole('dialog', { name: 'Your webhook' });
    await expect(secret.getByLabel('Secret')).toHaveValue(String(body.webhookSecret));
    await expect(secret).toContainText("Copy this secret now. You won't see it again.");
    const example = secret.getByRole('region', { name: 'Signing example' });
    await expect(example).toContainText('X-PipesHub-Signature: v1=$sig');
    await expect(example).toContainText('/api/v1/agent-harness/triggers/');
    await expect(secret).not.toContainText('X-PipesHub-Trigger-Token');
    await shoot(page, 'ui3-sch-5-signed-webhook');
    await secret.getByRole('button', { name: 'Done' }).click();
    await expect(secret).toBeHidden();
    await expect(page.locator('body')).not.toContainText(String(body.webhookSecret));
  });

  test("UI3-SCH-6: the caller can't sign: the webhook takes a token sent in a header", async ({ page }) => {
    const dialog = await openForm(page);
    await dialog.getByLabel('Name', { exact: true }).fill(scheduleName('token hook'));
    await dialog.getByLabel('What to ask').fill('Triage the payload.');
    await dialog.getByRole('radio', { name: 'When a webhook is called' }).click();
    await dialog.getByRole('checkbox', { name: /The caller can't sign/ }).click();
    await expect(dialog).toContainText('Anyone who sees the token can call it. Signing is safer.');
    await shoot(page, 'ui3-sch-6-token-choice');
    const { sent, body } = await createFrom(page, dialog);
    expect(sent.trigger).toEqual({ kind: 'webhook', auth: 'token' });
    expect(typeof body.webhookToken).toBe('string');
    const secret = page.getByRole('dialog', { name: 'Your webhook' });
    await expect(secret).toContainText('X-PipesHub-Trigger-Token');
    await expect(secret.getByRole('region', { name: 'Signing example' })).toHaveCount(0);
    await shoot(page, 'ui3-sch-6-token-webhook');
    await secret.getByRole('button', { name: 'Done' }).click();
  });

  test('UI3-SCH-7: the history says why a slot was skipped, that one waits for the last run, and that one was caught up', async ({ page, apiContext }) => {
    const name = scheduleName('history');
    const created = await apiContext.post(SCHEDULES, {
      data: { name, input: { query: 'Summarise' }, trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'run_all' }, overlap: 'queue' },
    });
    expect(created.status(), await created.text()).toBe(201);
    const id = ((await created.json()) as { schedule: { id: string } }).schedule.id;
    const firing = (over: Record<string, unknown>) => ({
      kind: 'cron', status: 'skipped', runId: null, conversationId: null, missed: 0, attempts: 1, settledAt: null, ...over,
    });
    await page.route(
      (url) => url.pathname.endsWith(`${SCHEDULES}/${id}/firings`),
      async (route: Route) =>
        route.fulfill({
          json: {
            firings: [
              firing({ id: 'f-wait', slotAt: '2026-10-26T09:00:00.000Z', createdAt: '2026-10-26T09:00:01.000Z', status: 'pending', reason: 'overlap_queued' }),
              firing({ id: 'f-skip', slotAt: '2026-10-26T08:00:00.000Z', createdAt: '2026-10-26T08:00:01.000Z', reason: 'overlap_skip' }),
              firing({ id: 'f-full', slotAt: '2026-10-26T07:00:00.000Z', createdAt: '2026-10-26T07:00:01.000Z', reason: 'overlap_queue_full' }),
              firing({ id: 'f-late', slotAt: '2026-10-25T07:00:00.000Z', createdAt: '2026-10-26T06:30:00.000Z', status: 'admitted', reason: null }),
            ],
          },
        }),
    );
    await page.goto('/workspace/schedules/');
    const row = page.getByRole('group', { name, exact: true });
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(row).toContainText('Run once for each missed time');
    await row.getByRole('button', { name: 'History' }).click();
    const items = row.getByRole('list', { name: `Runs of ${name}` }).getByRole('listitem');
    await expect(items).toHaveCount(4);
    await expect(items.nth(0)).toContainText('Waiting for the last run to finish');
    await expect(items.nth(1)).toContainText('Skipped: the last run was still going');
    await expect(items.nth(2)).toContainText('Skipped: a run was already waiting');
    await expect(items.nth(3)).toContainText('Scheduled (caught up)');
    await expect(row).not.toContainText('Missed while PipesHub was down');
    await shoot(page, 'ui3-sch-7-history', row);
  });

});
