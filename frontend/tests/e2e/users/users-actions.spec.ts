import { test, expect } from '../fixtures/base.fixture';
import { getRows, waitForTableLoaded } from '../helpers/entity-table.helper';

test.describe('Users Actions', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/users/');
    // The logged-in admin is always a user, so an empty table is a failure, not a skip.
    await waitForTableLoaded(page, 15_000);
  });

  test('clicking a row opens user detail', async ({ page }) => {
    await getRows(page).first().click();

    await expect
      .poll(
        async () =>
          page.url().includes('panel=detail') ||
          page.url().includes('userId=') ||
          (await page
            .locator('[data-side-panel], [role="complementary"]')
            .first()
            .isVisible()
            .catch(() => false)),
        { timeout: 5_000 },
      )
      .toBe(true);
  });

  test('row hover shows the actions menu, which opens', async ({ page }) => {
    const row = getRows(page).first();
    await row.hover();

    const actionsButton = row
      .locator('button')
      .filter({ has: page.locator('span.material-icons-outlined').filter({ hasText: 'more_horiz' }) })
      .first();
    await expect(actionsButton, 'each user row should offer an actions menu').toBeVisible({
      timeout: 5_000,
    });

    await actionsButton.click();
    await expect(page.locator('[data-radix-popper-content-wrapper]').first()).toBeVisible({
      timeout: 5_000,
    });
  });
});
