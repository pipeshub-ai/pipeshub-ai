/**
 * u4-schedules.spec.ts — U4 PW-1 / H3 on the e2e lane (deployment/e2e/run.sh).
 *
 * Real Node, nothing mocked: Labs `ENABLE_SCHEDULED_RUNS` is turned on through
 * the platform settings route (read-merge-write, as the lane's seed does) and
 * put back afterwards; the seeded admin creates a cron schedule in the dialog,
 * sees it listed, pauses it and deletes it.
 */
import type { APIRequestContext, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { adminApi, setLabsFlag } from '../helpers/labs-flag.helper';

test.describe.configure({ mode: 'serial' });

const SCHEDULES = '/api/v1/agent-harness/schedules';
const FLAG = 'ENABLE_SCHEDULED_RUNS';

async function mySchedules(api: APIRequestContext): Promise<Array<{ id: string; name: string; status: string }>> {
  const res = await api.get(`${SCHEDULES}?scope=mine`);
  expect(res.ok()).toBe(true);
  return ((await res.json()) as { schedules: Array<{ id: string; name: string; status: string }> }).schedules;
}

const scheduleRow = (page: Page, name: string) => page.getByRole('group', { name, exact: true });

test.describe('Schedules (U4 PW-1)', () => {
  let flagBefore = false;
  const name = `PW-1 weekly digest ${Date.now().toString(36)}`;

  test.beforeAll(async () => {
    const api = await adminApi();
    flagBefore = await setLabsFlag(api, FLAG, true);
    await api.dispose();
  });

  test.afterAll(async () => {
    const api = await adminApi();
    for (const left of (await mySchedules(api)).filter((s) => s.name === name)) {
      await api.delete(`${SCHEDULES}/${left.id}`);
    }
    await setLabsFlag(api, FLAG, flagBefore);
    await api.dispose();
  });

  test('PW-1: create a cron schedule, see it listed, pause it, delete it', async ({ page, apiContext }) => {
    await page.goto('/workspace/schedules/');
    await expect(page.getByText('Your schedules', { exact: true })).toBeVisible({ timeout: 30_000 });

    await page.getByRole('button', { name: 'New schedule' }).click();
    const dialog = page.getByRole('dialog', { name: 'New schedule' });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel('Name', { exact: true }).fill(name);
    await dialog.getByLabel('What to ask').fill('Summarise what changed in the release notes this week.');
    await dialog.getByLabel('Cron expression').fill('0 9 * * 1');
    await dialog.getByLabel('Time zone').fill('Europe/Berlin');
    const created = page.waitForResponse((r) => r.url().endsWith(SCHEDULES) && r.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create' }).click();
    const answer = await created;
    expect(answer.status(), await answer.text()).toBe(201);
    const sent = answer.request().postDataJSON() as { name: string; input: { query: string }; trigger: Record<string, unknown> };
    expect(sent).toMatchObject({
      name,
      input: { query: 'Summarise what changed in the release notes this week.' },
      trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'skip' },
    });
    await expect(dialog).toBeHidden();

    const row = scheduleRow(page, name);
    await expect(row).toBeVisible();
    await expect(row).toContainText('Active');
    await expect(row).toContainText('0 9 * * 1');
    await expect(row).toContainText('Europe/Berlin');
    await expect(row).toContainText('Assistant');
    const stored = (await mySchedules(apiContext)).find((s) => s.name === name);
    expect(stored?.status).toBe('active');

    const paused = page.waitForResponse((r) => r.url().endsWith(`/${stored?.id}/pause`) && r.request().method() === 'POST');
    await row.getByRole('button', { name: 'Pause' }).click();
    expect((await paused).ok()).toBe(true);
    await expect(row.getByRole('button', { name: 'Resume' })).toBeVisible();
    await expect(row).toContainText('Paused');
    expect((await mySchedules(apiContext)).find((s) => s.name === name)?.status).toBe('paused');

    await page.reload();
    await expect(scheduleRow(page, name)).toContainText('Paused', { timeout: 30_000 });

    await scheduleRow(page, name).getByRole('button', { name: 'Delete' }).click();
    const confirm = page.getByRole('alertdialog');
    await expect(confirm).toContainText(`Delete ${name}?`);
    const deleted = page.waitForResponse((r) => r.url().endsWith(`${SCHEDULES}/${stored?.id}`) && r.request().method() === 'DELETE');
    await confirm.getByRole('button', { name: 'Delete' }).click();
    expect((await deleted).ok()).toBe(true);
    await expect(scheduleRow(page, name)).toHaveCount(0);
    expect((await mySchedules(apiContext)).some((s) => s.name === name)).toBe(false);
  });
});
