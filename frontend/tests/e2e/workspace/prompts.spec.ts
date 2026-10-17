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
    await expect(page.locator('textarea').first()).toBeEditable({ timeout: 5_000 });
  });

  test('displays Agent mode section', async ({ page }) => {
    const section = page.getByTestId('prompt-section-agent');
    await expect(section).toBeVisible({ timeout: 5_000 });
    await expect(section.locator('textarea')).toBeVisible();
  });

  test('shows Agent Builder scope callout', async ({ page }) => {
    const callout = page.locator('text=/Agent Builder/i').first();
    await expect(callout).toBeVisible({ timeout: 5_000 });
  });

  test('can edit prompt and save', async ({ page }) => {
    const textarea = page.getByTestId('prompt-section-agent').locator('textarea');
    await expect(textarea, 'the agent prompt box should be shown').toBeVisible({ timeout: 5_000 });
    const original = await textarea.inputValue();
    const probe = `E2E test system prompt ${Date.now()}`;

    // The save bar only appears once there are unsaved edits.
    const save = page.getByRole('button', { name: 'Save', exact: true });

    try {
      await textarea.fill(probe);
      await expect(save).toBeVisible({ timeout: 5_000 });
      await save.click();
      await expect(page.getByText('Prompt saved').first()).toBeVisible({ timeout: 10_000 });

      await page.reload();
      await expect(textarea, 'the saved prompt should persist after a reload').toHaveValue(probe, {
        timeout: 10_000,
      });
    } finally {
      // Put the original prompt back, and check that saved too.
      await textarea.fill(original);
      // The bar shows only if this differs from what is saved (it won't if saving the probe failed).
      if (await save.isVisible()) {
        await save.click();
        await expect(page.getByText('Prompt saved').first()).toBeVisible({ timeout: 10_000 });
      }
      await page.reload();
      await expect(textarea).toHaveValue(original, { timeout: 10_000 });
    }
  });

  test('reset to default works', async ({ page }) => {
    const section = page.getByTestId('prompt-section-agent');
    const textarea = section.locator('textarea');
    const resetButton = section.getByRole('button', { name: 'Reset to Default', exact: true });

    await expect(textarea).toHaveCount(1);
    await expect(resetButton).toHaveCount(1);
    await expect(textarea).toBeVisible({ timeout: 5_000 });

    await textarea.fill('E2E reset probe');
    await expect(resetButton).toBeEnabled();

    await resetButton.click();
    await expect(textarea).toHaveValue('');
  });
});
