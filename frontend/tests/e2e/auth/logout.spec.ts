import { test, expect } from '../fixtures/base.fixture';

test.describe('Logout', () => {
  test('logout clears auth and redirects to login', async ({ page }) => {
    await page.goto('/chat/');
    await page.waitForURL('**/chat/**', { timeout: 10_000 });

    // Clear auth state to simulate logout
    await page.evaluate(() => {
      localStorage.removeItem('jwt_access_token');
      localStorage.removeItem('jwt_refresh_token');
    });

    // Reload to trigger auth guard
    await page.reload();

    // Should redirect to /login
    await page.waitForURL('**/login/**', { timeout: 10_000 });
    await expect(page).toHaveURL(/\/login\//);
  });
});
