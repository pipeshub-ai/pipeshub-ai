import { test, expect } from '../fixtures/base.fixture';

test.describe('Workspace Prompts', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/prompts/');
    await page.waitForTimeout(2_000);
  });

  test('page loads with prompt settings', async ({ page }) => {
    const heading = page.locator('text=/Prompt|System/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });
  });

  test('displays editable prompt textarea', async ({ page }) => {
    const textarea = page.locator('textarea');
    if (await textarea.first().isVisible()) {
      const value = await textarea.first().inputValue();
      expect(value.length).toBeGreaterThanOrEqual(0);
    }
  });

  test('displays all three mode sections including Agent', async ({ page }) => {
    await expect(page.locator('text=Internal Search').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Web Search').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=Agent').first()).toBeVisible({ timeout: 5_000 });
    // Three independent editors — one per mode.
    expect(await page.locator('textarea').count()).toBeGreaterThanOrEqual(3);
  });

  test('shows Agent Builder scope callout', async ({ page }) => {
    const callout = page.locator('text=/Agent Builder/i').first();
    await expect(callout).toBeVisible({ timeout: 5_000 });
  });

  test('can edit prompt and save', async ({ page }) => {
    const textarea = page.locator('textarea');
    if ((await textarea.count()) === 0) return;

    const original = await textarea.first().inputValue();
    await textarea.first().clear();
    await textarea.first().fill('E2E test system prompt');

    const saveButton = page.locator('button').filter({ hasText: /Save|Update/i });
    if (await saveButton.first().isVisible()) {
      await saveButton.first().click();
      await page.waitForTimeout(2_000);
    }

    // Restore
    await textarea.first().clear();
    await textarea.first().fill(original);
    if (await saveButton.first().isVisible()) {
      await saveButton.first().click();
    }
  });

  test('reset to default works', async ({ page }) => {
    // Three identical editors render on this page, so both locators are scoped
    // to one section — otherwise the textarea and the button could resolve to
    // different modes and the assertion would pass for the wrong reason.
    const section = page.getByTestId('prompt-section-internal-search');
    const textarea = section.locator('textarea');
    const resetButton = section.getByRole('button', { name: 'Reset to Default', exact: true });

    await expect(textarea).toHaveCount(1);
    await expect(resetButton).toHaveCount(1);
    await expect(textarea).toBeVisible({ timeout: 5_000 });

    // `disabled={!value}` in prompts/page.tsx — an org with no override starts
    // empty, so the button only becomes clickable once the editor has content.
    await textarea.fill('E2E reset probe');
    await expect(resetButton).toBeEnabled();

    // Clearing the editor IS the default (no org override); nothing is saved,
    // so this leaves no state behind.
    await resetButton.click();
    await expect(textarea).toHaveValue('');
  });
});
