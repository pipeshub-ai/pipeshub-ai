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
  // The account's sign-in limit (10 a minute by default) is shared with every worker's own sign-in
  // (fixtures/base.fixture.ts): a 429 is waited out and the form sent again, up to two minutes.
  const deadline = Date.now() + 120_000;
  for (;;) {
    const answered = page.waitForResponse((r) => r.url().includes('/userAccount/authenticate'), { timeout: 15_000 });
    await page.click('button[type="submit"]');
    const res = await answered;
    if (res.status() !== 429) break;
    if (Date.now() > deadline) throw new Error('sign-in still refused by the rate limit after two minutes');
    await page.waitForTimeout(20_000);
  }
  await page.waitForURL('**/chat/**', { timeout: 15_000 });
}
