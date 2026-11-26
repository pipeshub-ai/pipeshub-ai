/**
 * WP-26 GIT-FLOW: picking the GitHub repository a coding chat works on, on the human-test stack
 * (scripts/human-test-stack.sh). Tagged `@ht1`: it reads that stack's fixtures, so playwright.config.ts leaves it
 * out where PXH_HT1_ADMIN_EMAIL is unset.
 *
 * Accounts come from the environment only: PXH_HT1_ADMIN_EMAIL / PXH_HT1_ADMIN_PASSWORD (has a signed-in GitHub
 * connection named "GitHub" listing acme/widgets, acme/widgets-docs and octo-dev/sandbox) and PXH_HT1_MEMBER_EMAIL /
 * PXH_HT1_MEMBER_PASSWORD (no GitHub connection). Pictures go to PXH_SHOTS_DIR (default test-results/wp26-repo), in
 * light and dark, at 1280×800 and 375×812. The case list is docs/agent-harness-v2/ui-cases/wp26-repo.json.
 *
 *   E2E_LLM_MODEL_KEY=<the seed's scripted llmKey> PLAYWRIGHT_NO_SERVER=1 E2E_HARNESS_LANE=1 BASE_URL=http://127.0.0.1:<port> \
 *     npx playwright test tests/e2e/harness/wp26-repo-picker.spec.ts --project=harness --no-deps
 */
import { mkdirSync } from 'fs';
import path from 'path';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { loginViaUI } from '../helpers/login.helper';
import { pinScriptedModel } from './support/lane';

test.describe.configure({ mode: 'serial' });
test.use({ storageState: { cookies: [], origins: [] } });

const SHOTS = process.env.PXH_SHOTS_DIR ?? 'test-results/wp26-repo';
const ADMIN = { email: process.env.PXH_HT1_ADMIN_EMAIL ?? '', password: process.env.PXH_HT1_ADMIN_PASSWORD ?? '' };
const MEMBER = { email: process.env.PXH_HT1_MEMBER_EMAIL ?? '', password: process.env.PXH_HT1_MEMBER_PASSWORD ?? '' };

const VARIANTS = [
  { name: 'desktop-light', viewport: { width: 1280, height: 800 }, dark: false },
  { name: 'desktop-dark', viewport: { width: 1280, height: 800 }, dark: true },
  { name: 'phone-light', viewport: { width: 375, height: 812 }, dark: false },
  { name: 'phone-dark', viewport: { width: 375, height: 812 }, dark: true },
] as const;

async function signIn(page: Page, who: { email: string; password: string }): Promise<void> {
  test.skip(!who.email || !who.password, 'needs the human-test stack account in the environment');
  // The stack's default model may be a paid one: every turn this spec sends goes to the stack's scripted model.
  await pinScriptedModel(page);
  await loginViaUI(page, who.email, who.password);
}

const composer = (page: Page) => page.locator('textarea').last();
const plusButton = (page: Page) => page.getByRole('button', { name: /^Attach files and capabilities/ }).first();
/** Why `/repo` is greyed: on its menu row, or under the composer once the name is typed whole. */
const slashReason = (page: Page) => page.getByTestId('slash-command-reason').or(page.getByTestId('slash-command-problem')).first();
/** The menu row's reason starts with its info icon, whose ligature is text ("info…"): the reason is how it ends. */
const endsWith = (text: string) => new RegExp(`${text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`);
const repoRow = (page: Page) => page.getByTestId('plus-menu-repository').last();

async function newChat(page: Page): Promise<void> {
  await page.goto('/chat/');
  await composer(page).waitFor({ timeout: 30_000 });
}

async function openPlusMenu(page: Page): Promise<void> {
  await plusButton(page).click();
  await expect(repoRow(page)).toBeVisible({ timeout: 15_000 });
}

async function closeMenus(page: Page): Promise<void> {
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
}

/** Each variant: the viewport and colour scheme set, the state rebuilt by `show`, one picture. */
async function shots(page: Page, name: string, show: () => Promise<void>): Promise<void> {
  mkdirSync(SHOTS, { recursive: true });
  for (const variant of VARIANTS) {
    await page.setViewportSize(variant.viewport);
    await page.emulateMedia({ colorScheme: variant.dark ? 'dark' : 'light' });
    await page.evaluate((d) => localStorage.setItem('pipeshub-theme-preference', d ? 'dark' : 'light'), variant.dark);
    await show();
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(SHOTS, `${name}-${variant.name}.png`) });
    await closeMenus(page);
  }
  await page.setViewportSize(VARIANTS[0].viewport);
}

async function openPicker(page: Page): Promise<void> {
  await openPlusMenu(page);
  await repoRow(page).click();
  await expect(page.getByTestId('repo-picker')).toBeVisible();
}

async function chooseWidgets(page: Page): Promise<void> {
  await openPicker(page);
  await page.getByTestId('repo-picker-search').fill('widgets');
  const row = page.locator('[data-testid="repo-picker-row"][data-repository="acme/widgets"]');
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();
  await expect(page.getByTestId('repo-picker-branch')).toContainText('main (default)', { timeout: 15_000 });
}

test('1. the + menu offers "Work on a repository"', { tag: '@ht1' }, async ({ page }) => {
  await signIn(page, ADMIN);
  await newChat(page);
  await openPlusMenu(page);
  await expect(repoRow(page)).toContainText('Work on a repository');
  await expect(page.getByTestId('plus-menu-repository-subtitle').last()).toHaveText('Clone a GitHub repository into this chat');
  await expect(repoRow(page)).not.toHaveAttribute('aria-disabled', 'true');
  await closeMenus(page);
  await shots(page, '01-plus-menu', async () => {
    await newChat(page);
    await openPlusMenu(page);
  });
});

test('2a. greyed: this chat has no coding engine', { tag: '@ht1' }, async ({ page }) => {
  await signIn(page, ADMIN);
  const noEngine = async () => {
    await newChat(page);
    await composer(page).fill('/engine none');
    await composer(page).press('Enter');
    await expect(page.getByTestId('pending-engine-chip')).toBeVisible({ timeout: 15_000 });
    await openPlusMenu(page);
  };
  await noEngine();
  await expect(repoRow(page)).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByTestId('plus-menu-repository-subtitle').last()).toHaveText('Needs a coding engine. Pick one first.');
  await closeMenus(page);
  await composer(page).fill('/repo');
  await expect(slashReason(page)).toHaveText(endsWith('Needs a coding engine. Pick one first.'));
  await composer(page).fill('');
  await shots(page, '02a-greyed-no-engine', noEngine);
});

test('2b. greyed: the organization has no coding engine turned on', { tag: '@ht1' }, async ({ page }) => {
  await signIn(page, ADMIN);
  // The one stub: the org's engine read, every engine reported unavailable.
  await page.route('**/api/v1/agent-harness/model-accounts/coding-engine', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const response = await route.fetch();
    const view = (await response.json()) as { engines?: Array<Record<string, unknown>> };
    view.engines = (view.engines ?? []).map((e) => ({ ...e, available: false, reason: 'turned off' }));
    await route.fulfill({ response, json: view });
  });
  const orgOff = async () => {
    await newChat(page);
    await openPlusMenu(page);
  };
  await orgOff();
  await expect(page.getByTestId('plus-menu-repository-subtitle').last()).toHaveText(
    "Your organization hasn't turned on coding engines.",
    { timeout: 15_000 },
  );
  await expect(repoRow(page)).toHaveAttribute('aria-disabled', 'true');
  await closeMenus(page);
  await shots(page, '02b-greyed-org-off', orgOff);
});

test('2c and 5. a saved chat: the chip until the copy, and greyed "already works on"', { tag: '@ht1' }, async ({ page }) => {
  test.skip(!process.env.E2E_LLM_MODEL_KEY, "sends a chat turn: needs E2E_LLM_MODEL_KEY (the scripted model's key)");
  await signIn(page, ADMIN);
  await newChat(page);
  await composer(page).fill('Reply with the single word: ready');
  await composer(page).press('Enter');
  await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
  await expect(page.getByRole('button', { name: /^Stop/ })).toHaveCount(0, { timeout: 120_000 });
  const chatUrl = page.url();

  await chooseWidgets(page);
  await page.getByTestId('repo-picker-use').click();
  await expect(page.getByTestId('repo-picker')).toBeHidden();
  await expect(page.getByTestId('repository-chip')).toContainText('acme/widgets from main');

  const taken = async () => {
    await page.goto(chatUrl);
    await expect(page.getByTestId('repository-chip')).toBeVisible({ timeout: 30_000 });
    await openPlusMenu(page);
    await expect(page.getByTestId('plus-menu-repository-subtitle').last()).toHaveText(
      'This chat already works on acme/widgets. Start a new chat for another repository.',
      { timeout: 15_000 },
    );
  };
  await taken();
  await expect(repoRow(page)).toHaveAttribute('aria-disabled', 'true');
  await closeMenus(page);
  await shots(page, '02c-greyed-has-repository', taken);

  // × drops it while nothing was copied yet: the row is usable again.
  await page.goto(chatUrl);
  await page.getByTestId('repository-chip-clear').click();
  await expect(page.getByTestId('repository-chip')).toHaveCount(0);
  await openPlusMenu(page);
  await expect(repoRow(page)).not.toHaveAttribute('aria-disabled', 'true');
});

test('3. the picker lists the repositories, searches, and marks the default branch', { tag: '@ht1' }, async ({ page }) => {
  await signIn(page, ADMIN);
  await newChat(page);
  await openPicker(page);
  await expect(page.getByTestId('repo-picker-connection')).toContainText('GitHub');
  await expect(page.getByTestId('repo-picker-row')).toHaveCount(3, { timeout: 15_000 });
  await expect(page.locator('[data-repository="acme/widgets"]').getByTestId('repo-picker-private')).toHaveText('private');
  await page.getByTestId('repo-picker-search').fill('widgets');
  await expect(page.getByTestId('repo-picker-row')).toHaveCount(2, { timeout: 15_000 });
  await page.locator('[data-repository="acme/widgets"]').click();
  await expect(page.getByTestId('repo-picker-branch')).toContainText('main (default)', { timeout: 15_000 });
  await page.getByTestId('repo-picker-branch').click();
  await expect(page.getByRole('option', { name: 'release/1.x' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('repo-picker-note')).toContainText("PipesHub copies acme/widgets into this chat's workspace");
  await closeMenus(page);
  await shots(page, '03-picker', async () => {
    await newChat(page);
    await chooseWidgets(page);
  });
});

test('4. without a GitHub connection: "Connect GitHub first"', { tag: '@ht1' }, async ({ page }) => {
  await signIn(page, MEMBER);
  // The member keeps "As my default" and the org names no default engine, so a new chat has none until one is
  // picked: the chat gets one with /engine, as a person would, so the picker can open.
  const memberChat = async () => {
    await newChat(page);
    await composer(page).fill('/engine opencode');
    await composer(page).press('Enter');
    await expect(page.getByTestId('pending-engine-chip')).toBeVisible({ timeout: 15_000 });
    await openPicker(page);
  };
  await memberChat();
  await expect(page.getByTestId('repo-picker-connect')).toContainText('Connect GitHub first.', { timeout: 15_000 });
  await shots(page, '04-connect-github', async () => {
    await memberChat();
    await expect(page.getByTestId('repo-picker-connect')).toBeVisible({ timeout: 15_000 });
  });
  await memberChat();
  await page.getByTestId('repo-picker-connect-github').click();
  await expect(page).toHaveURL(/\/workspace\/actions\/personal\/?\?toolsetType=github/, { timeout: 30_000 });
});

test('5. a new chat: the chip over the composer before the first message', { tag: '@ht1' }, async ({ page }) => {
  await signIn(page, ADMIN);
  const held = async () => {
    await newChat(page);
    await chooseWidgets(page);
    await page.getByTestId('repo-picker-use').click();
    await expect(page.getByTestId('repository-chip')).toContainText('acme/widgets from main');
  };
  await held();
  await composer(page).fill('/repo');
  await expect(slashReason(page)).toHaveText(
    endsWith('This chat already works on acme/widgets. Start a new chat for another repository.'),
  );
  await composer(page).fill('');
  await page.getByTestId('repository-chip-clear').click();
  await expect(page.getByTestId('repository-chip')).toHaveCount(0);
  await shots(page, '05-chip', held);
});

test('6. activity rows: copied, pushed and the pull request, plainly', { tag: '@ht1' }, async ({ page }) => {
  test.skip(
    !process.env.PXH_REPO_ENGINE_TURN || !process.env.E2E_LLM_MODEL_KEY,
    'needs an engine turn against a cloneable repository, on the scripted model (E2E_LLM_MODEL_KEY)',
  );
  test.setTimeout(900_000);
  await signIn(page, ADMIN);
  await newChat(page);
  await chooseWidgets(page);
  await page.getByTestId('repo-picker-use').click();
  await composer(page).fill(
    'Add a line "Checked by PipesHub." to the end of README.md, commit it, push your branch, then open a pull request.',
  );
  await composer(page).press('Enter');
  await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
  const rows = page.getByTestId('engine-repo');
  await expect(rows.filter({ hasText: /^Copied acme\/widgets · branch pipeshub\/session-/ })).toHaveCount(1, { timeout: 600_000 });
  await expect(rows.filter({ hasText: /^Pushed to pipeshub\/session-/ }).first()).toBeVisible({ timeout: 600_000 });
  await expect(rows.filter({ hasText: /^Opened pull request #\d+ in acme\/widgets/ })).toHaveCount(1, { timeout: 600_000 });
  // Plain words only: no JSON or raw command output in the repository rows.
  for (const text of await rows.allTextContents()) expect(text).not.toMatch(/[{}]|remote:/);
  const url = page.url();
  await shots(page, '06-activity', async () => {
    await page.goto(url);
    await expect(rows.first()).toBeVisible({ timeout: 60_000 });
    await rows.first().scrollIntoViewIfNeeded();
  });
});
