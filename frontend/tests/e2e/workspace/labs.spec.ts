import { test, expect } from '../fixtures/base.fixture';

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
    await expect(page.getByText('File Upload Limit').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByPlaceholder('max. 1000')).toBeVisible();
  });
});
