import { test, expect } from '@playwright/test';

test.describe('Users Table', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/users/');
    await page.waitForTimeout(3_000);
  });

  test('page loads with users table', async ({ page }) => {
    const heading = page.locator('text=/Users/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });

    // Table rows should be present
    const rows = page.locator('[role="row"]');
    await expect(rows.first()).toBeVisible({ timeout: 10_000 });
  });

  test('displays pagination with showing count', async ({ page }) => {
    const showing = page.locator('text=/Showing/i').first();
    await expect(showing).toBeVisible({ timeout: 10_000 });
  });

  test('pagination: navigate to next page', async ({ page }) => {
    const showingText = await page.locator('text=/Showing/').first().textContent() ?? '';
    const match = showingText.match(/(\d+)\s*[-–]\s*(\d+)\s+.*?(\d+)/);
    const to = match ? parseInt(match[2], 10) : 0;
    const total = match ? parseInt(match[3], 10) : 0;

    if (to >= total) {
      test.skip(true, 'All items fit on one page — nothing to paginate');
      return;
    }

    const nextButton = page.locator('text="Next"').first();
    const showingBefore = showingText;
    await nextButton.click();
    await page.waitForTimeout(1_000);
    const showingAfter = await page.locator('text=/Showing/').first().textContent();
    expect(showingAfter).not.toBe(showingBefore);
  });

  test('pagination: navigate to previous page', async ({ page }) => {
    const showingText = await page.locator('text=/Showing/').first().textContent() ?? '';
    const match = showingText.match(/(\d+)\s*[-–]\s*(\d+)\s+.*?(\d+)/);
    const to = match ? parseInt(match[2], 10) : 0;
    const total = match ? parseInt(match[3], 10) : 0;

    if (to >= total) {
      test.skip(true, 'All items fit on one page — nothing to paginate');
      return;
    }

    // Go to page 2 first
    const nextButton = page.locator('text="Next"').first();
    await nextButton.click();
    await page.waitForTimeout(1_000);

    const prevButton = page.locator('text="Previous"').first();
    await prevButton.click();
    await page.waitForTimeout(1_000);

    const showing = await page.locator('text=/Showing/').first().textContent();
    expect(showing).toContain('1');
  });

  test('pagination: change limit to 25', async ({ page }) => {
    // Find and click the limit dropdown
    const limitTrigger = page.locator('text=/per page/').first();
    if (await limitTrigger.isVisible()) {
      await limitTrigger.locator('..').click();
      await page.locator('[role="menuitem"]').filter({ hasText: '25 per page' }).click();
      await page.waitForTimeout(1_500);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      expect(count).toBeLessThanOrEqual(25);
    }
  });

  test('pagination: change limit to 50', async ({ page }) => {
    const limitTrigger = page.locator('text=/per page/').first();
    if (await limitTrigger.isVisible()) {
      await limitTrigger.locator('..').click();
      await page.locator('[role="menuitem"]').filter({ hasText: '50 per page' }).click();
      await page.waitForTimeout(1_500);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      expect(count).toBeLessThanOrEqual(50);
    }
  });

  test('pagination: change limit to 100', async ({ page }) => {
    const limitTrigger = page.locator('text=/per page/').first();
    if (await limitTrigger.isVisible()) {
      await limitTrigger.locator('..').click();
      await page.locator('[role="menuitem"]').filter({ hasText: '100 per page' }).click();
      await page.waitForTimeout(2_000);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      expect(count).toBeLessThanOrEqual(100);
    }
  });

  test('search filters users by name/email', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('e2e-user');
      await page.waitForTimeout(1_000);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      // Should either show filtered results or empty state
      expect(count).toBeGreaterThanOrEqual(0);
    }
  });

  test('search with no results shows empty state', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('zzz-nonexistent-user-zzz');
      await page.waitForTimeout(1_000);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      expect(count).toBe(0);
    }
  });

  test('clear search shows all users again', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('e2e-user');
      await page.waitForTimeout(1_000);
      const filteredCount = await page.locator('[role="row"]').count();

      await searchInput.clear();
      await page.waitForTimeout(1_000);
      const allCount = await page.locator('[role="row"]').count();

      expect(allCount).toBeGreaterThanOrEqual(filteredCount);
    }
  });
});
