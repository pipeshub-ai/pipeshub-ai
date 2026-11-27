import { test, expect } from '../fixtures/base.fixture';
import { loginViaUI } from '../helpers/login.helper';
import { SESSION_COOKIES, sessionFamily, webStorage } from '../helpers/session.helper';

/**
 * R41OPS-FU-7 (ADR-WEB-02): signing in keeps no session token where a script
 * on the page could read it, and the person stays signed in across reloads and
 * tabs through the HttpOnly session cookie.
 *
 * Signs in on a context of its own and never signs out on the server: a server
 * sign-out ends every session of the shared e2e account, in every worker.
 */
test.describe('Session persistence', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('stays signed in across a reload and a new tab, with no token in page storage', async ({ page, context }) => {
    // A sign-in may wait out the account's limit while the workers sign in (helpers/login.helper.ts).
    test.slow();
    const email = process.env.TEST_USER_EMAIL;
    const password = process.env.TEST_USER_PASSWORD;
    expect(email && password, 'TEST_USER_EMAIL and TEST_USER_PASSWORD must be set').toBeTruthy();

    await loginViaUI(page, email!, password!);

    // The refresh token is an HttpOnly, SameSite=Strict cookie; __Host- (Secure, Path=/) on https.
    const cookies = (await context.cookies()).filter((c) => (SESSION_COOKIES as readonly string[]).includes(c.name));
    expect(cookies, 'one session cookie after sign-in').toHaveLength(1);
    const [cookie] = cookies;
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.sameSite).toBe('Strict');
    if (new URL(page.url()).protocol === 'https:') {
      expect(cookie.name).toBe('__Host-pipeshub_refresh');
      expect(cookie.secure).toBe(true);
      expect(cookie.path).toBe('/');
    } else {
      expect(cookie.name).toBe('pipeshub_refresh');
    }
    expect(await page.evaluate(() => document.cookie)).not.toContain('pipeshub_refresh');

    const noTokenStored = async () => {
      const stored = await webStorage(page);
      expect(Object.keys(stored)).not.toContain('jwt_access_token');
      expect(Object.keys(stored)).not.toContain('jwt_refresh_token');
      // No JWT-shaped value under any key.
      const jwts = Object.entries(stored).filter(([, v]) => /eyJ[\w-]+\.[\w-]+\.[\w-]+/.test(v)).map(([k]) => k);
      expect(jwts, 'keys holding a JWT').toEqual([]);
      // Booleans only: a failure message never carries a session value.
      const current = (await context.cookies()).find((c) => (SESSION_COOKIES as readonly string[]).includes(c.name));
      expect((current?.value.length ?? 0) > 20, 'a session cookie is held').toBe(true);
      const values = Object.values(stored).join('\n');
      expect([cookie.value, current?.value ?? ''].some((v) => v && values.includes(v)), 'a session value in page storage').toBe(false);
    };
    await noTokenStored();

    await page.reload();
    await page.waitForURL('**/chat/**', { timeout: 15_000 });
    await expect(page.locator('#email-field')).toHaveCount(0);
    await noTokenStored();

    const second = await context.newPage();
    await second.goto('/chat/');
    await second.waitForURL('**/chat/**', { timeout: 15_000 });
    await expect(second.locator('#email-field')).toHaveCount(0);

    // Without the cookie (another browser, or it was cleared) the same page is signed out.
    await context.clearCookies();
    await page.reload();
    await page.waitForURL('**/login/**', { timeout: 15_000 });
  });

  test('a browser holding an older build’s localStorage tokens is moved to the cookie', async ({ page, context, browser }) => {
    test.slow();
    const email = process.env.TEST_USER_EMAIL;
    const password = process.env.TEST_USER_PASSWORD;
    expect(email && password, 'TEST_USER_EMAIL and TEST_USER_PASSWORD must be set').toBeTruthy();

    // Mint the old shape: a refresh token in the body, as a client that does not ask for the cookie gets it.
    const signIn = await browser.newContext();
    const legacy = await signIn.newPage();
    await loginViaUI(legacy, email!, password!);
    const cookie = (await signIn.cookies()).find((c) => (SESSION_COOKIES as readonly string[]).includes(c.name));
    expect(cookie, 'a session cookie to stand in for the old localStorage refresh token').toBeTruthy();
    await signIn.close();

    await page.goto('/login/');
    await page.evaluate((refresh) => {
      window.localStorage.setItem('jwt_refresh_token', refresh);
    }, cookie!.value);
    await context.clearCookies();

    await page.goto('/chat/');
    await page.waitForURL('**/chat/**', { timeout: 15_000 });
    const stored = await webStorage(page);
    expect(Object.keys(stored)).not.toContain('jwt_refresh_token');
    // The move is a refresh, so the cookie it sets is the legacy token's replacement (ADR-WEB-02 D9): a new value
    // in the same sign-in. Compared as booleans so no session value reaches a failure message.
    const moved = (await context.cookies()).find((c) => (SESSION_COOKIES as readonly string[]).includes(c.name));
    expect(!!moved?.value, 'a session cookie after the move').toBe(true);
    expect(moved!.httpOnly).toBe(true);
    expect(moved!.value === cookie!.value, 'the moved cookie is the replacement, not the legacy token').toBe(false);
    const family = sessionFamily(cookie!.value);
    expect(!!family && sessionFamily(moved!.value) === family, 'the replacement continues the legacy sign-in').toBe(true);
  });
});
