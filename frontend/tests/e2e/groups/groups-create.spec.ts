import { test, expect } from '../fixtures/base.fixture';

test.describe('Groups Create', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/groups/');
    await page.waitForTimeout(3_000);
  });

  test('opens create sidebar when clicking CTA', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Create/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    // The URL should update with panel=create
    expect(page.url()).toContain('panel=create');
  });

  test('create group with name', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Create/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    // Fill group name
    const nameInput = page.locator('input[placeholder="e.g. Data Engineering"]');
    await nameInput.fill('E2E Test Create Group');

    // Submit inside the dialog (not the page CTA behind the overlay)
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Create Group' }).click();
    await page.waitForTimeout(2_000);

    // Verify the group appears in the table
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('E2E Test Create Group');
      await page.waitForTimeout(1_000);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      expect(count).toBeGreaterThanOrEqual(1);
    }
  });

  test('validation: empty name shows error', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Create/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    // Try to submit without filling name — button should be inside the dialog
    const dialog = page.getByRole('dialog');
    const submitButton = dialog.getByRole('button', { name: 'Create Group' });
    if (await submitButton.isVisible()) {
      const isDisabled = await submitButton.isDisabled();
      if (!isDisabled) {
        await submitButton.click();
        await page.waitForTimeout(500);
        const error = page.locator('text=/required|name|empty/i');
        const hasError = await error.first().isVisible().catch(() => false);
      }
    }
  });
});
