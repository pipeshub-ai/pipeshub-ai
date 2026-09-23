import { test, expect } from '../fixtures/base.fixture';

test.describe('Users Bulk Operations', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/users/');
    await page.waitForTimeout(3_000);
  });

  test('select all checkbox toggles all rows', async ({ page }) => {
    // The logged-in admin is always listed, so an empty table is a failure.
    await expect(page.locator('[role="row"]').first()).toBeVisible({ timeout: 15_000 });
    const rowCount = await page.locator('[role="row"]').count();

    // Click the header "select all" checkbox (first checkbox on page)
    const headerCheckbox = page.locator('button[role="checkbox"]').first();
    await headerCheckbox.click();
    await page.waitForTimeout(300);

    // All rows should now be selected
    const selectedRows = page.locator('[role="row"][aria-selected="true"]');
    const selectedCount = await selectedRows.count();
    expect(selectedCount).toBe(rowCount);

    // Toggle off
    await headerCheckbox.click();
    await page.waitForTimeout(300);

    const deselectedCount = await page.locator('[role="row"][aria-selected="true"]').count();
    expect(deselectedCount).toBe(0);
  });

  test('individual row checkbox selection', async ({ page }) => {
    const rows = page.locator('[role="row"]');
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    const rowCount = await rows.count();
    test.skip(rowCount < 2, 'needs at least two users; only the admin exists in this environment');

    // Click checkbox on first row
    const firstRowCheckbox = rows.first().locator('button[role="checkbox"]');
    await firstRowCheckbox.click();
    await page.waitForTimeout(200);

    // First row should be selected
    await expect(rows.first()).toHaveAttribute('aria-selected', 'true');

    // Second row should NOT be selected
    const secondSelected = await rows.nth(1).getAttribute('aria-selected');
    expect(secondSelected).not.toBe('true');
  });
});
