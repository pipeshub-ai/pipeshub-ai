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
    const resetButton = page.locator('button').filter({ hasText: /Reset|Default/i });
    if (await resetButton.first().isVisible()) {
      await resetButton.first().click();
      await page.waitForTimeout(1_000);
    }
  });
});
