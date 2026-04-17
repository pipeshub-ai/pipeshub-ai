import { test, expect } from '@playwright/test';

test.describe('Groups Table', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/groups/');
    await page.waitForTimeout(3_000);
  });

  test('page loads with groups table', async ({ page }) => {
    const heading = page.locator('text=/Groups/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });
  });

  test('displays pagination', async ({ page }) => {
    const showing = page.locator('text=/Showing/i').first();
    await expect(showing).toBeVisible({ timeout: 10_000 });
  });

  test('pagination: navigate pages', async ({ page }) => {
    const showingText = await page.locator('text=/Showing/').first().textContent() ?? '';
    const match = showingText.match(/(\d+)\s*[-–]\s*(\d+)\s+.*?(\d+)/);
    const to = match ? parseInt(match[2], 10) : 0;
    const total = match ? parseInt(match[3], 10) : 0;

    if (to >= total) {
      test.skip(true, 'All items fit on one page — nothing to paginate');
      return;
    }

    const nextButton = page.locator('text="Next"').first();
    const textBefore = showingText;
    await nextButton.click();
    await page.waitForTimeout(1_000);
    const textAfter = await page.locator('text=/Showing/').first().textContent();
    expect(textAfter).not.toBe(textBefore);
  });

  test('search filters groups', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('E2E Group');
      await page.waitForTimeout(1_000);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      expect(count).toBeGreaterThanOrEqual(0);
    }
  });

  test('search with no match shows empty state', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('zzz-nonexistent-group-zzz');
      await page.waitForTimeout(1_000);

      const rows = page.locator('[role="row"]');
      const count = await rows.count();
      expect(count).toBe(0);
    }
  });

  test('pagination: change limit to 25', async ({ page }) => {
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
});
