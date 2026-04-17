import { test as setup, expect } from '../fixtures/base.fixture';

const AUTH_FILE = '.auth/user.json';

setup('authenticate', async ({ page }) => {
  const email = process.env.TEST_USER_EMAIL;
  const password = process.env.TEST_USER_PASSWORD;

  if (!email || !password) {
    throw new Error(
      'TEST_USER_EMAIL and TEST_USER_PASSWORD must be set. ' +
      'Copy .env.test.example to .env.test and fill in credentials.'
    );
  }

  await page.goto('/login/');

  // Wait for the login form to render (it depends on an initAuth API call)
  await page.waitForSelector('#email-field', { timeout: 15_000 });

  await page.fill('#email-field', email);
  await page.fill('#password-field', password);
  await page.click('button[type="submit"]');

  // Wait for redirect to /chat after successful login
  await page.waitForURL('**/chat/**', { timeout: 15_000 });
  await expect(page).toHaveURL(/\/chat\//);

  // Save auth state (includes localStorage with auth-storage key)
  await page.context().storageState({ path: AUTH_FILE });
});
