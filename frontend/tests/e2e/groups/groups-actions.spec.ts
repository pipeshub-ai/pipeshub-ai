import { test, expect } from '../fixtures/base.fixture';

test.describe('Groups Actions', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/groups/');
    await page.waitForTimeout(3_000);
  });

  test('clicking a row opens group detail', async ({ page }) => {
    const rows = page.locator('[role="row"]');
    const rowCount = await rows.count();
    if (rowCount === 0) {
      test.skip();
      return;
    }

    await rows.first().click();
    await page.waitForTimeout(1_000);

    // Detail should open — URL should have panel=detail or groupId
    const urlChanged = page.url().includes('panel=detail') || page.url().includes('groupId=');
    const panelVisible = await page.locator('[data-side-panel], [role="complementary"]')
      .first().isVisible().catch(() => false);

    expect(urlChanged || panelVisible).toBeTruthy();
  });

  test('delete group with confirmation', async ({ page }) => {
    // Search for a test group to delete
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('E2E Test Create Group');
      await page.waitForTimeout(1_000);
    }

    const rows = page.locator('[role="row"]');
    const rowCount = await rows.count();
    if (rowCount === 0) {
      test.skip();
      return;
    }

    // Click the row to open detail
    await rows.first().click();
    await page.waitForTimeout(1_000);

    // Look for delete button
    const deleteButton = page.locator('button').filter({ hasText: /Delete|Remove/ });
    if (await deleteButton.first().isVisible()) {
      await deleteButton.first().click();
      await page.waitForTimeout(500);

      // Confirm dialog — click confirm
      const confirmButton = page.locator('button').filter({ hasText: /Confirm|Delete|Yes/ });
      if (await confirmButton.first().isVisible()) {
        await confirmButton.first().click();
        await page.waitForTimeout(2_000);
      }
    }
  });
});
