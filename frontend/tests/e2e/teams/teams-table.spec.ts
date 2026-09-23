import { test, expect } from '../fixtures/base.fixture';
import { changeLimit } from '../helpers/pagination.helper';

test.describe('Teams Table', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/teams/');
    await page.waitForTimeout(3_000);
  });

  test('page loads with teams table', async ({ page }) => {
    const heading = page.locator('text=/Teams/i').first();
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

  test('search filters teams and clearing it restores them', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="Search"]');
    await expect(searchInput).toBeVisible({ timeout: 10_000 });
    const rows = page.locator('[role="row"]');
    const before = await rows.count();

    await searchInput.fill('zzz-nonexistent-team-zzz');
    await expect(rows).toHaveCount(0, { timeout: 10_000 });

    // Clearing the search brings back exactly the teams listed before it.
    await searchInput.clear();
    await expect(rows).toHaveCount(before, { timeout: 10_000 });
  });

  test('search with no match shows empty state', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="Search"]');
    await expect(searchInput).toBeVisible({ timeout: 10_000 });
    await searchInput.fill('zzz-nonexistent-team-zzz');
    await page.waitForTimeout(1_000);

    const rows = page.locator('[role="row"]');
    const count = await rows.count();
    expect(count).toBe(0);
  });

  // The default limit is 25, so each test changes to a different value first:
  // a click that changes nothing fails instead of passing.
  test('pagination: change limit to 50', async ({ page }) => {
    await changeLimit(page, 50);
  });

  test('pagination: change limit to 25', async ({ page }) => {
    await changeLimit(page, 50);
    await changeLimit(page, 25);
  });

  test('pagination: change limit to 100', async ({ page }) => {
    await changeLimit(page, 100);
  });
});
