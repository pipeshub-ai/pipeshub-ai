/**
 * WP-21 (OWD-14) on a real stack: the agent builder's versions, driven through the UI the way an editor uses them.
 * No model is called: creating, saving, publishing and rolling back an agent never runs it.
 *
 *   01 no versions yet       a new agent is "Not published yet"; creating it publishes version 1
 *   02 first publish         the created agent is live as version 1, and the versions panel lists it
 *   03 draft                 a rename saved as a draft shows "Unpublished changes"; chats keep version 1
 *   04 version list          after publishing version 2 with a note, and saving another draft: both versions,
 *                            who and when, version 2 live, and the unpublished-changes callout
 *   05 rollback confirm      rolling back to version 1 warns that the unpublished changes will be discarded
 *   06 after rollback        version 3 is live, "rolled back from version 1", the draft is version 1's again
 *                            and the panel says the unpublished changes were discarded
 *   07 chat version label    an answer of the agent says "<agent> · version 3" in its activity line (mock §5); the
 *                            stack's scripted model answers, so no real model is called
 *   08 schedule pin          a schedule pinned to version 1 says so and that version 3 is live; its owner opens
 *                            the choices: keep 1, move to 3, or always the live version (mock §4)
 *   09 schedule moved        after "Move to version 3" the schedule runs version 3, and the API says so
 *
 * 08–09 are their own test: they need Labs `ENABLE_SCHEDULED_RUNS`, which they turn on for the run (as
 * u4-schedules does, so the signed-in user must be an admin unless the stack has it on) and put back after.
 * It runs once per look: desktop and 375 px, light and dark. When WP21_SHOTS names a directory, each case saves
 * `wp21-<case>-<look>.png` there; the case list is docs/agent-harness-v2/ui-cases/wp21-versions.json.
 */
import { existsSync, mkdirSync } from 'fs';
import { randomBytes } from 'crypto';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { adminApi, setLabsFlag } from '../helpers/labs-flag.helper';
import { script, sendTurn } from './support/lane';

const SHOTS = process.env.WP21_SHOTS ?? '';
const SCHEDULES = '/api/v1/agent-harness/schedules';
const SCHEDULED_RUNS = 'ENABLE_SCHEDULED_RUNS';
const LOOKS = [
  { name: 'desktop-light', viewport: { width: 1440, height: 900 }, dark: false },
  { name: 'desktop-dark', viewport: { width: 1440, height: 900 }, dark: true },
  { name: 'phone-light', viewport: { width: 375, height: 812 }, dark: false },
  { name: 'phone-dark', viewport: { width: 375, height: 812 }, dark: true },
] as const;

type VersionsBody = {
  versions: { version: number; source: string; rolledBackFrom: number | null; note: string | null; publishedBy: string | null }[];
  latestVersion: number;
  unpublishedChanges: boolean;
};

async function shoot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });
  // A picture taken before the icon font loads shows the icons' names instead.
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: 'nextjs-portal { display: none !important; }' });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

async function versions(api: APIRequestContext, agentKey: string): Promise<VersionsBody> {
  const response = await api.get(`/api/v1/agents/${agentKey}/versions`);
  expect(response.ok(), `listing versions: ${response.status()}`).toBe(true);
  return (await response.json()) as VersionsBody;
}

const status = (page: Page) => page.getByTestId('agent-version-status');
const nameField = (page: Page) => page.getByPlaceholder('e.g. Support bot');
const panel = (page: Page) => page.getByTestId('agent-versions-panel');
const row = (page: Page, version: number) =>
  panel(page).locator(`[data-testid="agent-version-row"][data-version="${version}"]`);
const confirmDialog = (page: Page) => page.getByRole('alertdialog');
// The buttons' accessible names carry their icon's ligature first ("publish Publish"), so names match at the end.
const headerButton = (page: Page, name: RegExp) => page.getByRole('button', { name });

async function openVersions(page: Page): Promise<Locator> {
  await headerButton(page, /Versions$/).click();
  await expect(panel(page)).toBeVisible();
  await expect(panel(page).getByTestId('agent-version-row').first()).toBeVisible({ timeout: 30_000 });
  return panel(page);
}

async function closeVersions(page: Page): Promise<void> {
  await page.keyboard.press('Escape');
  await expect(panel(page)).toBeHidden();
}

async function rename(page: Page, name: string): Promise<void> {
  await nameField(page).fill(name);
  await expect(status(page)).toContainText('Unpublished changes');
}

async function lookAt(page: Page, dark: boolean): Promise<void> {
  await page.addInitScript((isDark) => {
    try {
      localStorage.setItem('pipeshub-theme-preference', isDark ? 'dark' : 'light');
    } catch {
      /* private mode: the emulated colour scheme still applies */
    }
  }, dark);
}

/** An agent made through the API with versions 1–3 (3 is a rollback to 1), for the schedule cases. */
async function agentWithThreeVersions(api: APIRequestContext, name: string): Promise<string> {
  const created = await api.post('/api/v1/agents/create', {
    data: { name, description: '', startMessage: '', systemPrompt: '', models: [], tags: [], shareWithOrg: false, isServiceAccount: false },
  });
  expect(created.ok(), await created.text()).toBe(true);
  const key = ((await created.json()) as { agent: { _key: string } }).agent._key;
  const renamed = await api.put(`/api/v1/agents/${key}`, { data: { name: `${name} v2` } });
  expect(renamed.ok(), await renamed.text()).toBe(true);
  const rolledBack = await api.post(`/api/v1/agents/${key}/versions/1/rollback`, { data: {} });
  expect(rolledBack.status(), await rolledBack.text()).toBe(201);
  expect((await versions(api, key)).latestVersion).toBe(3);
  return key;
}

for (const look of LOOKS) {
  test.describe(`WP-21 agent versions (${look.name})`, () => {
    test.use({ viewport: look.viewport, colorScheme: look.dark ? 'dark' : 'light' });

    let agentKey: string | null = null;
    let scheduleId: string | null = null;
    // Only the schedule cases need the flag; a stack that already has it on is left untouched.
    let scheduledRunsBefore: boolean | null = null;
    test.beforeEach(async ({}, testInfo) => {
      if (!testInfo.title.includes('schedule')) return;
      const api = await adminApi();
      const settings = await api.get('/api/v1/configurationManager/platform/settings');
      const flags = settings.ok()
        ? ((await settings.json()) as { featureFlags?: Record<string, boolean> }).featureFlags
        : undefined;
      if (flags?.[SCHEDULED_RUNS] !== true) scheduledRunsBefore = await setLabsFlag(api, SCHEDULED_RUNS, true);
      await api.dispose();
    });
    test.afterEach(async ({ apiContext }) => {
      if (scheduleId) await apiContext.delete(`${SCHEDULES}/${scheduleId}`);
      if (agentKey) await apiContext.delete(`/api/v1/agents/${agentKey}`);
      scheduleId = null;
      agentKey = null;
      if (scheduledRunsBefore !== null) {
        const api = await adminApi();
        await setLabsFlag(api, SCHEDULED_RUNS, scheduledRunsBefore);
        await api.dispose();
        scheduledRunsBefore = null;
      }
    });

    test(`OWD-14 UI: publish, draft, version list and rollback (${look.name})`, async ({ page, apiContext }) => {
      const shot = (id: string) => shoot(page, `wp21-${id}-${look.name}`);
      await lookAt(page, look.dark);
      const tag = randomBytes(3).toString('hex');
      const original = `Support triage ${tag}`;

      // 01: a new agent has no versions yet.
      await page.goto('/agents/new/');
      const create = page.getByRole('dialog', { name: 'Create agent' });
      await expect(create).toBeVisible({ timeout: 60_000 });
      await expect(status(page)).toHaveText('Not published yet');
      await create.getByPlaceholder('e.g. Support bot').fill(original);
      await shot('01-no-versions');

      // 02: creating it publishes version 1.
      await create.getByRole('button', { name: /Create agent$/ }).click();
      await page.waitForURL(/\/agents\/edit\/?\?agentKey=/, { timeout: 60_000 });
      agentKey = new URL(page.url()).searchParams.get('agentKey');
      expect(agentKey).toBeTruthy();
      await expect(nameField(page)).toHaveValue(original, { timeout: 60_000 });
      await expect(status(page)).toContainText('Live: version 1');
      await expect(status(page)).not.toContainText('Unpublished changes');
      await expect(headerButton(page, /Publish$/)).toBeDisabled();
      await openVersions(page);
      await expect(row(page, 1)).toContainText('Live');
      await expect(panel(page).getByTestId('agent-version-row')).toHaveCount(1);
      await shot('02-first-publish');
      await closeVersions(page);
      expect((await versions(apiContext, agentKey!)).latestVersion).toBe(1);

      // 03: a rename saved as a draft is not what chats run.
      await rename(page, `${original} v2`);
      await headerButton(page, /Save draft$/).click();
      await expect(page.getByText('Draft saved. Chats keep running version 1 until you publish.')).toBeVisible();
      await expect(status(page)).toContainText('Live: version 1');
      await expect(status(page)).toContainText('Unpublished changes');
      await expect(headerButton(page, /Save draft$/)).toBeDisabled();
      await expect(headerButton(page, /Publish$/)).toBeEnabled();
      await shot('03-draft-unpublished');
      const drafted = await versions(apiContext, agentKey!);
      expect(drafted.latestVersion).toBe(1);
      expect(drafted.unpublishedChanges).toBe(true);

      // 04: publish it as version 2 with a note; then save another draft and read the list.
      await headerButton(page, /Publish$/).click();
      const publish = confirmDialog(page);
      await expect(publish.getByRole('heading', { name: 'Publish version 2?' })).toBeVisible();
      await publish.getByRole('textbox', { name: 'What changed (optional)' }).fill('Renamed for the support team.');
      await publish.getByRole('button', { name: 'Publish', exact: true }).click();
      const published = page.getByRole('dialog', { name: 'Version 2 published' });
      await expect(published).toBeVisible({ timeout: 30_000 });
      await published.getByRole('button', { name: 'Continue editing' }).click();
      await expect(status(page)).toContainText('Live: version 2');
      await expect(status(page)).not.toContainText('Unpublished changes');

      await rename(page, `${original} v3 draft`);
      await headerButton(page, /Save draft$/).click();
      await expect(status(page)).toContainText('Unpublished changes');
      await openVersions(page);
      await expect(panel(page).getByTestId('agent-version-row')).toHaveCount(2);
      await expect(row(page, 2)).toContainText('Live');
      await expect(row(page, 2)).toContainText('Renamed for the support team.');
      await expect(row(page, 2)).not.toContainText('Unknown user');
      await expect(panel(page).getByTestId('agent-versions-unpublished')).toContainText(
        'You have unpublished changes. Rolling back to an earlier version discards them.',
      );
      await shot('04-version-list');

      // 05: roll back to version 1; the dialog warns the draft is discarded.
      await row(page, 1).getByRole('button', { name: /Roll back to this version$/ }).click();
      const rollback = confirmDialog(page);
      await expect(rollback.getByRole('heading', { name: 'Roll back to version 1?' })).toBeVisible();
      await expect(rollback).toContainText('publishes it as version 3');
      await expect(rollback).toContainText('Your unpublished changes will be discarded.');
      await rollback.getByRole('textbox', { name: 'Why (optional)' }).fill('Back to the original name.');
      await shot('05-rollback-confirm');

      // 06: version 3 is version 1 again, and the draft with it.
      await rollback.getByRole('button', { name: 'Roll back', exact: true }).click();
      // The confirm is gone before Escape: while it closes, its own layer takes the key (WP-71, flaky on the lane).
      await expect(rollback).toHaveCount(0, { timeout: 30_000 });
      await expect(panel(page).getByTestId('agent-rollback-outcome')).toHaveText(
        'Rolled back to version 1. It is live as version 3. Your unpublished changes were discarded.',
        { timeout: 30_000 },
      );
      await expect(row(page, 3)).toContainText('Live');
      await expect(row(page, 3)).toContainText('Rolled back from version 1');
      await expect(row(page, 3)).toContainText('Back to the original name.');
      await expect(panel(page).getByTestId('agent-versions-unpublished')).toHaveCount(0);
      await shot('06-after-rollback');
      await closeVersions(page);
      await expect(nameField(page)).toHaveValue(original);
      await expect(status(page)).toContainText('Live: version 3');
      await expect(status(page)).not.toContainText('Unpublished changes');

      const after = await versions(apiContext, agentKey!);
      expect(after.latestVersion).toBe(3);
      expect(after.unpublishedChanges).toBe(false);
      expect(after.versions.map((v) => [v.version, v.source, v.rolledBackFrom])).toEqual([
        [3, 'rollback', 1],
        [2, 'publish', null],
        [1, 'publish', null],
      ]);

      // 07: the agent's answer says which version answered.
      const answer = `Answered by ${tag}.`;
      await sendTurn(page, agentKey!, script(`wp21 ${tag}`, `say ${answer}`));
      await expect(page.getByText(answer, { exact: true })).toBeVisible({ timeout: 90_000 });
      const label = page.getByTestId('run-agent-version').last();
      await expect(label).toHaveText(`${original} · version 3`, { timeout: 30_000 });
      await shot('07-chat-version-label');
    });

    test(`OWD-14 UI: a schedule keeps its version until its owner moves it (${look.name})`, async ({ page, apiContext }) => {
      const shot = (id: string) => shoot(page, `wp21-${id}-${look.name}`);
      await lookAt(page, look.dark);
      const tag = randomBytes(3).toString('hex');
      agentKey = await agentWithThreeVersions(apiContext, `Support triage ${tag}`);

      // 08: a schedule pinned to version 1 says a newer version is live; its owner opens the choices.
      const created = await apiContext.post(SCHEDULES, {
        data: {
          name: `Weekly triage ${tag}`,
          agentKey,
          agentVersion: 1,
          input: { query: 'Summarise the new tickets.' },
          trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'skip' },
        },
      });
      expect(created.status(), await created.text()).toBe(201);
      scheduleId = ((await created.json()) as { schedule: { id: string } }).schedule.id;
      await page.goto('/workspace/schedules/');
      const scheduleRow = page.getByRole('group', { name: `Weekly triage ${tag}`, exact: true });
      const pin = scheduleRow.getByTestId('schedule-version');
      await expect(pin).toContainText('Runs version 1', { timeout: 30_000 });
      await expect(pin).toContainText('Version 3 is live');
      await pin.getByRole('button', { name: 'Change version' }).click();
      await expect(pin.getByRole('radio')).toHaveCount(3);
      await expect(pin.getByRole('radio', { name: /Keep version 1/ })).toBeChecked();
      await expect(pin.getByRole('radio', { name: /Move to version 3/ })).toBeVisible();
      await expect(pin.getByRole('radio', { name: /Always use the live version/ })).toBeVisible();
      await pin.getByRole('radio', { name: /Move to version 3/ }).click();
      await scheduleRow.scrollIntoViewIfNeeded();
      await shot('08-schedule-pin');

      // 09: moved; the schedule's next runs use version 3.
      const moved = page.waitForResponse((r) => r.url().endsWith(`/${scheduleId}/agent-version`) && r.request().method() === 'PUT');
      await pin.getByRole('button', { name: 'Save', exact: true }).click();
      expect((await moved).ok()).toBe(true);
      await expect(pin).toContainText('Runs version 3');
      await expect(pin).not.toContainText('is live');
      await expect(pin.getByRole('radiogroup')).toHaveCount(0);
      await scheduleRow.scrollIntoViewIfNeeded();
      await shot('09-schedule-moved');
      const listed = (await (await apiContext.get(`${SCHEDULES}?scope=mine`)).json()) as {
        schedules: { id: string; agentVersion: unknown }[];
      };
      expect(listed.schedules.find((x) => x.id === scheduleId)?.agentVersion).toBe(3);
    });
  });
}
