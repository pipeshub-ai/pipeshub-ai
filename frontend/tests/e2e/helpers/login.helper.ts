import type { Page } from '@playwright/test';

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
  await page.fill('#email-field', email);
  await page.fill('#password-field', password);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/chat/**', { timeout: 15_000 });
}
