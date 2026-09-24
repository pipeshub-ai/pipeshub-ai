import { test, expect } from '../fixtures/base.fixture';

test.describe('Groups Enterprise Placeholder', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/groups/');
    await page.waitForTimeout(2_000);
  });

  test('shows the Enterprise upgrade message', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Groups' }).first()).toBeVisible({
      timeout: 10_000,
    });
    await expect(
      page.getByRole('heading', {
        name: /Group permissions are available in the Enterprise Edition/i,
      })
    ).toBeVisible();
    await expect(
      page.getByText(/Upgrade to the Enterprise Edition to use group permissions/i)
    ).toBeVisible();
  });

  test('does not show Create Group or a groups table', async ({ page }) => {
    await expect(page.getByRole('button', { name: /Create Group/i })).toHaveCount(0);
    await expect(page.locator('[role="row"]')).toHaveCount(0);
    await expect(page.getByText(/Showing/i)).toHaveCount(0);
  });

  test('contact link opens the docs contact page', async ({ page }) => {
    const contact = page.getByRole('link', { name: '@pipeshub.com' });
    await expect(contact).toBeVisible();
    await expect(contact).toHaveAttribute('href', 'https://docs.pipeshub.com/contact-us');
  });
});
