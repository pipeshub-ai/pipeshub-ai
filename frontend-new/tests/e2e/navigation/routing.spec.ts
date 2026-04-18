import { test, expect } from '../fixtures/base.fixture';

test.describe('Route Access', () => {
  const authenticatedRoutes = [
    '/chat/',
    '/knowledge-base/',
    '/workspace/general/',
    '/workspace/profile/',
    '/workspace/users/',
    '/workspace/teams/',
    '/workspace/groups/',
  ];

  for (const route of authenticatedRoutes) {
    test(`authenticated: ${route} loads successfully`, async ({ page }) => {
      await page.goto(route);
      // Should NOT redirect to login
      await page.waitForTimeout(3_000);
      await expect(page).not.toHaveURL(/\/login/);
    });
  }
});

test.describe('Unauthenticated Route Redirect', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('unauthenticated access to /chat redirects to /login', async ({ page }) => {
    await page.goto('/chat/');
    await page.waitForURL('**/login/**', { timeout: 10_000 });
    await expect(page).toHaveURL(/\/login/);
  });

  test('unauthenticated access to /workspace redirects to /login', async ({ page }) => {
    await page.goto('/workspace/general/');
    await page.waitForURL('**/login/**', { timeout: 10_000 });
    await expect(page).toHaveURL(/\/login/);
  });
});
