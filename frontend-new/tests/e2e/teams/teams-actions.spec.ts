import { test, expect } from '@playwright/test';

test.describe('Teams Actions', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/teams/');
    await page.waitForTimeout(3_000);
  });

  test('clicking a row opens team detail', async ({ page }) => {
    const rows = page.locator('[role="row"]');
    const rowCount = await rows.count();
    if (rowCount === 0) {
      test.skip();
      return;
    }

    await rows.first().click();
    await page.waitForTimeout(1_000);

    const urlChanged = page.url().includes('panel=detail') || page.url().includes('teamId=');
    const panelVisible = await page.locator('[data-side-panel], [role="complementary"]')
      .first().isVisible().catch(() => false);

    expect(urlChanged || panelVisible).toBeTruthy();
  });

  test('edit team name', async ({ page }) => {
    // Search for our test team
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('E2E Test Create Team');
      await page.waitForTimeout(1_000);
    }

    const rows = page.locator('[role="row"]');
    const rowCount = await rows.count();
    if (rowCount === 0) {
      test.skip();
      return;
    }

    await rows.first().click();
    await page.waitForTimeout(1_000);

    // Look for edit mode toggle
    const editButton = page.locator('button').filter({ hasText: /Edit/ });
    if (await editButton.first().isVisible()) {
      await editButton.first().click();
      await page.waitForTimeout(500);

      // Modify name
      const nameInput = page.locator('input[type="text"]').first();
      await nameInput.clear();
      await nameInput.fill('E2E Test Create Team Updated');

      const saveButton = page.locator('button').filter({ hasText: /^(Save|Update)$/ });
      if (await saveButton.first().isVisible()) {
        await saveButton.first().click();
        await page.waitForTimeout(2_000);
      }
    }
  });

  test('delete team with confirmation', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="Search"]');
    if (await searchInput.isVisible()) {
      await searchInput.fill('E2E Test Create Team');
      await page.waitForTimeout(1_000);
    }

    const rows = page.locator('[role="row"]');
    const rowCount = await rows.count();
    if (rowCount === 0) {
      test.skip();
      return;
    }

    await rows.first().click();
    await page.waitForTimeout(1_000);

    const deleteButton = page.locator('button').filter({ hasText: /Delete|Remove/ });
    if (await deleteButton.first().isVisible()) {
      await deleteButton.first().click();
      await page.waitForTimeout(500);

      const confirmButton = page.locator('button').filter({ hasText: /Confirm|Delete|Yes/ });
      if (await confirmButton.first().isVisible()) {
        await confirmButton.first().click();
        await page.waitForTimeout(2_000);
      }
    }
  });
});
