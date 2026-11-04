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

  test('lists the named entity extraction flag', async ({ page }) => {
    // An advanced flag: it sits in the collapsed "advanced feature flags" section.
    await page.getByRole('button', { name: /Show advanced feature flags/i }).click();
    await expect(page.getByText('Extract Named Entities').first()).toBeVisible({ timeout: 10_000 });
  });

  test('displays file upload limit setting', async ({ page }) => {
    await expect(page.getByText('File upload limit').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByPlaceholder('max. 1000')).toBeVisible();
  });
});
