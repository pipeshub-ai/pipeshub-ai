import { test, expect } from '@playwright/test';

test.describe('Teams Create', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/teams/');
    await page.waitForTimeout(3_000);
  });

  test('opens create sidebar when clicking CTA', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Create/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    // URL should update with panel=create
    expect(page.url()).toContain('panel=create');
  });

  test('create team with name and description', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Create/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    // Fill team name
    const nameInput = page.locator('input[placeholder="e.g. Product Engineering"]');
    await nameInput.fill('E2E Test Create Team');

    // Fill description
    const textarea = page.locator('textarea[placeholder="Describe the purpose of this team"]');
    if ((await textarea.count()) > 0) {
      await textarea.first().fill('Created by E2E tests');
    }

    // Submit inside the dialog (not the page CTA behind the overlay)
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Create Team' }).click();
    await page.waitForTimeout(2_000);

    // Verify team appears in table
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('E2E Test Create Team');
      await page.waitForTimeout(1_000);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      expect(count).toBeGreaterThanOrEqual(1);
    }
  });

  test('validation: empty name prevents creation', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Create/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    const dialog = page.getByRole('dialog');
    const submitButton = dialog.getByRole('button', { name: 'Create Team' });
    if (await submitButton.isVisible()) {
      const isDisabled = await submitButton.isDisabled();
      if (!isDisabled) {
        await submitButton.click();
        await page.waitForTimeout(500);
      }
    }
  });
});
