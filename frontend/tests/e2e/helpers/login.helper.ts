import type { Page } from '@playwright/test';

/**
 * Bring the login form to its password field for *email*. One login page shows
 * email and password together; an email-first one shows the password only
 * after the email is submitted (Continue).
 */
export async function reachPasswordStep(page: Page, email: string): Promise<void> {
  await page.fill('#email-field', email);
  const password = page.locator('#password-field');
  if (await password.isVisible()) return;
  await page.click('button[type="submit"]');
  await password.waitFor({ state: 'visible', timeout: 15_000 });
}

/**
 * Perform a full login flow via the browser UI.
 * Useful for unauthenticated test suites that need a fresh login.
 */
export async function loginViaUI(
  page: Page,
  email: string,
  password: string
): Promise<void> {
  await page.goto('/login/');
  await page.waitForSelector('#email-field', { timeout: 15_000 });
  await reachPasswordStep(page, email);
  await page.fill('#password-field', password);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/chat/**', { timeout: 15_000 });
}
