import { test, expect } from '@playwright/test';

test.describe('Workspace Profile Settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/profile/');
    await page.waitForTimeout(2_000);
  });

  test('page loads with profile information', async ({ page }) => {
    const heading = page.locator('text=/Profile|Account/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });
  });

  test('displays full name field', async ({ page }) => {
    const nameInput = page.locator('input[placeholder="eg: John Doe"]');
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
  });

  test('can edit and save name', async ({ page }) => {
    const nameInput = page.locator('input[placeholder="eg: John Doe"]');
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
    const originalValue = await nameInput.inputValue();

    await nameInput.clear();
    await nameInput.fill('E2E Test User');

    // The floating save bar should appear
    const saveButton = page.locator('button').filter({ hasText: 'Save' });
    await expect(saveButton.first()).toBeVisible({ timeout: 3_000 });
    await saveButton.first().click();
    await page.waitForTimeout(2_000);

    // Restore
    await nameInput.clear();
    await nameInput.fill(originalValue);
    await expect(saveButton.first()).toBeVisible({ timeout: 3_000 });
    await saveButton.first().click();
    await page.waitForTimeout(1_000);
  });
});
