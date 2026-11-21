/**
 * R46-LANE (ADR-WEB-02 D9): every refresh replaces the session cookie, and a replaced cookie used after its 60 s
 * grace ends that sign-in. One storage state shared by every test held a cookie that went stale within a minute of
 * the first page load; the next test that refreshed with it ended the sign-in, and every later test, in every
 * worker, started signed out (364 passed before R44-IDENT, 105 after).
 *
 * So each worker signs in once, its own session family, and every test starts from the cookie the worker's previous
 * test ended with. A test that ends its session (clears the cookie, signs out) makes the next one sign in again.
 */

/** The parts of a Playwright storage state this reads. */
export interface SessionState {
  cookies: { name: string; value: string }[];
  origins: { origin: string; localStorage: { name: string; value: string }[] }[];
}

export const SESSION_COOKIES = ['__Host-pipeshub_refresh', 'pipeshub_refresh'] as const;

/** Builds before ADR-WEB-02 kept the tokens in localStorage; a state carrying them is not copied forward. */
const LEGACY_TOKEN_KEYS = new Set(['jwt_access_token', 'jwt_refresh_token']);

/**
 * The projects that run signed in name the setup project's file (`storageState: '.auth/user.json'`); the base
 * fixture lends those tests the worker's own session instead. A spec's `test.use({ storageState })` is left alone.
 */
export function isSharedSignIn(storageState: unknown): boolean {
  return typeof storageState === 'string' && /(^|[\\/])\.auth[\\/]user\.json$/.test(storageState);
}

export function hasSessionCookie(state: SessionState | null | undefined): boolean {
  return !!state?.cookies.some((c) => (SESSION_COOKIES as readonly string[]).includes(c.name) && c.value.length > 0);
}

/**
 * A worker's state: the session cookie of its own sign-in, and the setup's page storage (the `pipeshub_signed_in`
 * hint a reload reads before it asks to refresh), without any legacy token.
 */
export function workerState<C extends SessionState['cookies'][number]>(
  signedIn: { cookies: C[] },
  setup: SessionState | null,
): { cookies: C[]; origins: SessionState['origins'] } {
  const cookies = signedIn.cookies.filter((c) => (SESSION_COOKIES as readonly string[]).includes(c.name));
  const origins = (setup?.origins ?? []).map((o) => ({
    ...o,
    localStorage: o.localStorage.filter((item) => !LEGACY_TOKEN_KEYS.has(item.name)),
  }));
  return { cookies, origins };
}

export class WorkerSession<S extends SessionState = SessionState> {
  private state: S | null = null;
  private count = 0;

  constructor(private readonly signIn: () => Promise<S>) {}

  /** The state a test starts from: the worker's current one, signing in first when there is none. */
  async lend(): Promise<S> {
    if (!this.state) {
      const fresh = await this.signIn();
      if (!hasSessionCookie(fresh)) throw new Error('the worker sign-in returned no session cookie');
      this.count += 1;
      this.state = fresh;
    }
    return this.state;
  }

  /** Whether a test's storage state is the one this worker lent it. */
  owns(state: unknown): boolean {
    return this.state !== null && state === this.state;
  }

  /**
   * After a test that ran on the lent state: `ended` is its context's state once a refresh succeeded with it, or
   * null when the session no longer refreshes. Without a live cookie the next test signs in again.
   */
  settle(ended: S | null): void {
    this.state = ended && hasSessionCookie(ended) ? ended : null;
  }

  /** How many times this worker has signed in. */
  get signIns(): number {
    return this.count;
  }
}
