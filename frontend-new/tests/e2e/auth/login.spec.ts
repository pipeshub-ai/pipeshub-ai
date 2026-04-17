import { test, expect } from '../fixtures/base.fixture';

test.describe('Login Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login/');
    await page.waitForSelector('#email-field', { timeout: 15_000 });
  });

  test('renders login form with email and password fields', async ({ page }) => {
    await expect(page.locator('#email-field')).toBeVisible();
    await expect(page.locator('#password-field')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('submit button is disabled when email is empty', async ({ page }) => {
    await page.fill('#password-field', 'somepassword');
    await expect(page.locator('button[type="submit"]')).toBeDisabled();
  });

  test('shows validation error for invalid email', async ({ page }) => {
    await page.fill('#email-field', 'not-an-email');
    await page.fill('#password-field', 'somepassword');
    // Button should be disabled for invalid email
    await expect(page.locator('button[type="submit"]')).toBeDisabled();
  });

  test('shows error for wrong password', async ({ page }) => {
    const email = process.env.TEST_USER_EMAIL;
    if (!email) {
      test.skip();
      return;
    }

    await page.fill('#email-field', email);
    await page.fill('#password-field', 'definitelywrongpassword123');
    await page.click('button[type="submit"]');

    const errorText = page.locator('text=/Incorrect|incorrect|wrong|Wrong|invalid|Invalid|failed|Failed/');
    await expect(errorText.first()).toBeVisible({ timeout: 10_000 });
  });

  test('successful login redirects to /chat', async ({ page }) => {
    const email = process.env.TEST_USER_EMAIL;
    const password = process.env.TEST_USER_PASSWORD;
    if (!email || !password) {
      test.skip();
      return;
    }

    await page.fill('#email-field', email);
    await page.fill('#password-field', password);
    await page.click('button[type="submit"]');

    await page.waitForURL('**/chat/**', { timeout: 15_000 });
    await expect(page).toHaveURL(/\/chat\//);
  });
});
