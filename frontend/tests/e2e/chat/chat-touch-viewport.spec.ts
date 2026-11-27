import { test, expect } from '@playwright/test';

test.use({ isMobile: true, hasTouch: true, storageState: { cookies: [], origins: [] } });

test('chat remains interactive when a touch viewport changes size and orientation', async ({ page }) => {
  // Local-only identity and API responses keep this layout test independent of a server.
  await page.addInitScript(() => {
    localStorage.setItem('jwt_access_token', 'e30.eyJ1c2VySWQiOiJ0ZXN0IiwiZXhwIjoyMDAwMDAwMDAwfQ.x');
    localStorage.setItem('jwt_refresh_token', 'test');
  });
  await page.route('**/api/**', (route) => route.fulfill({ json: {
    status: 'success', models: [], conversations: [], pagination: {},
    fullName: 'Test User', role: 'member', hasLoggedIn: true,
  } }));
  await page.goto('/chat/');
  const composer = page.locator('textarea').last();
  await expect(composer).toBeVisible();

  for (const viewport of [
    { width: 360, height: 780 }, { width: 390, height: 844 },
    { width: 390, height: 300 }, { width: 844, height: 390 },
    { width: 640, height: 280 }, { width: 768, height: 1024 },
    { width: 1024, height: 768 },
  ]) {
    await page.setViewportSize(viewport);
    await composer.click();
    await composer.fill('Viewport test');
    await expect(composer).toHaveValue('Viewport test');
  }
});
