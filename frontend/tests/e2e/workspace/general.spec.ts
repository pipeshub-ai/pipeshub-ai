import { test, expect } from '../fixtures/api-context.fixture';

test.describe('Workspace General Settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/general/');
    await page.waitForTimeout(2_000);
  });

  test('page loads with organization settings @smoke', async ({ page }) => {
    const heading = page.locator('text=/General|Organization|Org/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });
  });

  test('displays editable registered name field', async ({ page }) => {
    const nameInput = page.locator('input[placeholder="e.g. Paypal Co. LLC"]');
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
    const value = await nameInput.inputValue();
    expect(value.length).toBeGreaterThan(0);
  });

  test('can edit and save org name', async ({ page, apiContext }) => {
    const before = (await (await apiContext.get('/api/v1/org')).json()) as {
      registeredName: string;
      shortName: string;
      contactEmail: string;
      permanentAddress?: Record<string, unknown>;
      dataCollectionConsent?: boolean;
    };
    const nameInput = page.locator('input[placeholder="e.g. Paypal Co. LLC"]');
    await expect(nameInput).toBeVisible({ timeout: 5_000 });
    await expect(nameInput).toHaveValue(before.registeredName);
    const edited = `${before.registeredName} E2E`;
    try {
      await nameInput.fill(edited);
      // The floating bar's Save, beside its Discard changes (other Save buttons on the page stay disabled).
      const bar = page.getByRole('button', { name: 'Discard changes' }).locator('xpath=..');
      const saved = page.waitForResponse((r) => r.request().method() === 'PUT' && new URL(r.url()).pathname === '/api/v1/org/');
      await bar.getByRole('button', { name: 'Save', exact: true }).click();
      expect((await saved).ok()).toBe(true);
      await page.reload();
      await expect(nameInput).toHaveValue(edited, { timeout: 10_000 });
    } finally {
      // WP-71: the org's name is put back whatever happened above.
      const restored = await apiContext.put('/api/v1/org/', {
        data: {
          registeredName: before.registeredName,
          shortName: before.shortName,
          contactEmail: before.contactEmail,
          permanentAddress: before.permanentAddress ?? {},
          dataCollectionConsent: before.dataCollectionConsent ?? false,
        },
      });
      expect(restored.ok(), `restoring the org name: ${restored.status()}`).toBe(true);
    }
  });
});
