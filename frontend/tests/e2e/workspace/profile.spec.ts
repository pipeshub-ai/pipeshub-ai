import { test, expect } from '../fixtures/base.fixture';

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
    const nameInput = page.locator('input[placeholder="e.g. John Doe"]');
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
  });

  test('can edit and save name', async ({ page }) => {
    const nameInput = page.locator('input[placeholder="e.g. John Doe"]');
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
    const originalValue = await nameInput.inputValue();
    // The floating bar's Save, beside its Discard changes (other Save buttons on the page stay disabled).
    const save = page.getByRole('button', { name: 'Discard changes' }).locator('xpath=..').getByRole('button', { name: 'Save', exact: true });
    const saveName = async (value: string) => {
      await nameInput.fill(value);
      const saved = page.waitForResponse((r) => r.request().method() === 'PUT' && /\/api\/v1\/users\/[^/]+$/.test(new URL(r.url()).pathname));
      await save.click();
      expect((await saved).ok()).toBe(true);
      await page.reload();
      await expect(nameInput).toHaveValue(value, { timeout: 10_000 });
    };

    try {
      await saveName('E2E Test User');
    } finally {
      // WP-71: the signed-in person's name is put back whatever happened above.
      if ((await nameInput.inputValue()) !== originalValue) await saveName(originalValue);
    }
  });
});
