import { test, expect } from '../fixtures/base.fixture';
import { reachPasswordStep } from '../helpers/login.helper';

test.describe('Login Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login/');
    await page.waitForSelector('#email-field', { timeout: 15_000 });
  });

  test('renders login form with email and password fields @smoke', async ({ page }) => {
    const email = process.env.TEST_USER_EMAIL;
    expect(email, 'TEST_USER_EMAIL must be set for the e2e suite').toBeTruthy();
    await expect(page.locator('#email-field')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
    // On an email-first page the password field appears once the email is in.
    await reachPasswordStep(page, email!);
    await expect(page.locator('#password-field')).toBeVisible();
  });

  test('submit button is disabled when email is empty', async ({ page }) => {
    const password = page.locator('#password-field');
    if (await password.isVisible()) await password.fill('somepassword');
    await expect(page.locator('button[type="submit"]')).toBeDisabled();
  });

  test('shows validation error for invalid email', async ({ page }) => {
    await page.fill('#email-field', 'not-an-email');
    const password = page.locator('#password-field');
    if (await password.isVisible()) {
      await password.fill('somepassword');
      await expect(page.locator('button[type="submit"]')).toBeDisabled();
      return;
    }
    // An email-first form checks the address on submit and stays on the email step.
    await page.locator('button[type="submit"]').click();
    await expect(page.getByText('Please enter a valid email address.')).toBeVisible();
    await expect(password).toBeHidden();
  });

  test('shows error for wrong password', async ({ page }) => {
    const email = process.env.TEST_USER_EMAIL;
    // Every authenticated test depends on this account, so a missing one is a setup failure.
    expect(email, 'TEST_USER_EMAIL must be set for the e2e suite').toBeTruthy();

    await reachPasswordStep(page, email!);
    await page.fill('#password-field', 'definitelywrongpassword123');
    await page.click('button[type="submit"]');

    const errorText = page.locator('text=/Incorrect|incorrect|wrong|Wrong|invalid|Invalid|failed|Failed/');
    await expect(errorText.first()).toBeVisible({ timeout: 10_000 });
  });

  test('successful login redirects to /chat @smoke', async ({ page }) => {
    const email = process.env.TEST_USER_EMAIL;
    const password = process.env.TEST_USER_PASSWORD;
    expect(email && password, 'TEST_USER_EMAIL and TEST_USER_PASSWORD must be set').toBeTruthy();

    await reachPasswordStep(page, email!);
    await page.fill('#password-field', password!);
    await page.click('button[type="submit"]');

    await page.waitForURL('**/chat/**', { timeout: 15_000 });
    await expect(page).toHaveURL(/\/chat\//);
  });
});
