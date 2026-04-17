import { test, expect } from '@playwright/test';

test.describe('Logout', () => {
  test('logout clears auth and redirects to login', async ({ page }) => {
    await page.goto('/chat/');
    await page.waitForURL('**/chat/**', { timeout: 10_000 });

    // Clear auth state to simulate logout
    await page.evaluate(() => {
      const authStore = localStorage.getItem('auth-storage');
      if (authStore) {
        const parsed = JSON.parse(authStore);
        parsed.state.accessToken = null;
        parsed.state.refreshToken = null;
        localStorage.setItem('auth-storage', JSON.stringify(parsed));
      }
    });

    // Reload to trigger auth guard
    await page.reload();

    // Should redirect to /login
    await page.waitForURL('**/login/**', { timeout: 10_000 });
    await expect(page).toHaveURL(/\/login\//);
  });
});
