import { test, expect } from '../fixtures/base.fixture';
import { refreshWithCookie, sessionFamily, signInAnotherDevice } from '../helpers/session.helper';

/**
 * EM-65 (R44-IDENT, ADR-WEB-02 D9): every refresh replaces the session cookie; two tabs refreshing at once stay
 * signed in; a replaced cookie used after its 60 s grace ends that one sign-in. Its audit entry is read through the
 * org's audit search, which moved out of this repository.
 *
 * No cookie or token value is ever printed: every comparison of one is reduced to a boolean before it is asserted.
 */

test.describe('Session rotation (EM-65)', () => {
  test.beforeEach(() => {
    expect(process.env.TEST_USER_EMAIL && process.env.TEST_USER_PASSWORD, 'TEST_USER_EMAIL and TEST_USER_PASSWORD must be set').toBeTruthy();
  });

  test('two tabs loading at once stay signed in, on the worker’s rotating session', async ({ context }) => {
    const [first, second] = [await context.newPage(), await context.newPage()];
    await Promise.all([first.goto('/chat/'), second.goto('/chat/')]);
    await Promise.all([first.waitForURL('**/chat/**', { timeout: 15_000 }), second.waitForURL('**/chat/**', { timeout: 15_000 })]);
    await Promise.all([first.reload(), second.reload()]);
    for (const tab of [first, second]) {
      await tab.waitForURL('**/chat/**', { timeout: 15_000 });
      await expect(tab.locator('#email-field')).toHaveCount(0);
    }
  });

  test.describe('over the refresh route', () => {
    // Its own sign-ins only: the worker's session is not touched.
    test.use({ storageState: { cookies: [], origins: [] } });

    test('two refreshes at once with one cookie both stay signed in, handed the same replacement', async () => {
      const a0 = await signInAnotherDevice();
      const answers = await Promise.all([refreshWithCookie(a0), refreshWithCookie(a0)]);
      expect(answers.map((a) => a.status), 'both tabs answered').toEqual([200, 200]);
      expect(answers.every((a) => a.accessToken), 'both get an access token').toBe(true);
      const [one, two] = answers.map((a) => a.cookie ?? '');
      expect(!!one && one === two, 'one replacement for both (one rotates, the other in the grace)').toBe(true);
      expect(one === a0, 'the cookie was replaced').toBe(false);
      expect(!!sessionFamily(a0) && sessionFamily(one) === sessionFamily(a0), 'the replacement keeps the sign-in').toBe(true);
    });

    // @slow: it waits out the 60 s grace. On its own worker the rest of the suite runs on.
    test('a cookie replayed after the grace ends that sign-in only @slow', async () => {
      test.slow();
      const a0 = await signInAnotherDevice();
      const b0 = await signInAnotherDevice();
      expect(!!sessionFamily(a0) && sessionFamily(a0) !== sessionFamily(b0), 'two sign-ins are two session families').toBe(true);

      const r1 = await refreshWithCookie(a0);
      expect(r1.status, 'the first refresh').toBe(200);
      const a1 = r1.cookie ?? '';
      const r2 = await refreshWithCookie(a1);
      expect(r2.status === 200 && !!r2.cookie && r2.cookie !== a1, 'the current cookie refreshes and rotates again').toBe(true);
      const a2 = r2.cookie ?? '';
      expect((await refreshWithCookie(a1)).status, 'the just-replaced cookie inside 60 s is answered (grace)').toBe(200);

      await new Promise((r) => setTimeout(r, 65_000));

      const replay = await refreshWithCookie(a0);
      expect(replay.status, 'the old cookie replayed after the grace is refused').toBe(401);
      expect(replay.cookie === '', 'the refusal clears the cookie').toBe(true);
      expect((await refreshWithCookie(a2)).status, 'that sign-in has ended: its current cookie is refused too').toBe(401);
      const other = await refreshWithCookie(b0);
      expect(other.status === 200 && !!other.cookie, 'the other device stays signed in').toBe(true);
      expect((await refreshWithCookie(a1)).status, 'a second replay is refused too').toBe(401);
    });
  });
});
