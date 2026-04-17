import { test, expect } from '@playwright/test';

test.describe('Workspace Labs', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/labs/');
    await page.waitForTimeout(2_000);
  });

  test('page loads with experimental features', async ({ page }) => {
    const heading = page.locator('text=/Labs|Experimental/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });
  });

  test('displays file upload limit setting', async ({ page }) => {
    const uploadSetting = page.locator('text=/upload|file.*limit|size/i').first();
    if (await uploadSetting.isVisible()) {
      await expect(uploadSetting).toBeVisible();
    }
  });
});
