import { test, expect } from '../fixtures/base.fixture';

const REFRESH = /\/api\/v1\/userAccount\/refresh\/token$/;

test.describe('Logout', () => {
  test('logout clears auth and redirects to login', async ({ page }) => {
    // The page restores its session as it loads: a refresh whose answer sets a new cookie. Cleared before that answer
    // landed, the cookie came back and the reload stayed signed in (R48MIGFIX-3), so wait for the page to settle first.
    const restored = page.waitForResponse((r) => REFRESH.test(new URL(r.url()).pathname), { timeout: 10_000 });
    await page.goto('/chat/');
    await page.waitForURL('**/chat/**', { timeout: 10_000 });
    expect((await restored).ok()).toBe(true);
    // Any later refresh (the proactive timer, another tab) holds this lock until its answer is in (ADR-WEB-02 D9).
    await page.evaluate(() => navigator.locks.request('pipeshub-session-refresh', { mode: 'exclusive' }, async () => undefined));

    // Drop the session on this device only (ADR-WEB-02: the HttpOnly cookie and the signed-in mark). A real
    // sign-out would end the shared e2e account's sessions in every other worker.
    await page.context().clearCookies();
    await page.evaluate(() => localStorage.removeItem('pipeshub_signed_in'));

    await page.reload();
    await page.waitForURL('**/login/**', { timeout: 10_000 });
    await expect(page).toHaveURL(/\/login\//);

    // Signed out, not merely sent to the sign-in page: no session cookie, no mark, and a signed-in page sends back here.
    const cookies = await page.context().cookies();
    expect(cookies.filter((c) => /pipeshub_refresh/.test(c.name))).toEqual([]);
    expect(await page.evaluate(() => localStorage.getItem('pipeshub_signed_in'))).toBeNull();
    await page.goto('/chat/');
    await page.waitForURL('**/login/**', { timeout: 10_000 });
  });
});
