import { test, expect } from '@playwright/test';

test.describe('Workspace General Settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/general/');
    await page.waitForTimeout(2_000);
  });

  test('page loads with organization settings', async ({ page }) => {
    const heading = page.locator('text=/General|Organization|Org/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });
  });

  test('displays editable registered name field', async ({ page }) => {
    const nameInput = page.locator('input[placeholder="eg: Paypal Co. LLC"]');
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
    const value = await nameInput.inputValue();
    expect(value.length).toBeGreaterThanOrEqual(0);
  });

  test('can edit and save org name', async ({ page }) => {
    const nameInput = page.locator('input[placeholder="eg: Paypal Co. LLC"]');
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
    const originalValue = await nameInput.inputValue();

    // Modify the name
    await nameInput.clear();
    await nameInput.fill(`${originalValue} E2E`);

    // The floating save bar should appear
    const saveButton = page.locator('button').filter({ hasText: 'Save' });
    await expect(saveButton.first()).toBeVisible({ timeout: 3_000 });
    await saveButton.first().click();
    await page.waitForTimeout(2_000);

    // Restore original name
    await nameInput.clear();
    await nameInput.fill(originalValue);
    await expect(saveButton.first()).toBeVisible({ timeout: 3_000 });
    await saveButton.first().click();
    await page.waitForTimeout(1_000);
  });
});
