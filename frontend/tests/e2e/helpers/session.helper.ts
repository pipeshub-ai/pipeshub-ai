import { request, type APIRequestContext, type APIResponse, type BrowserContext, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

import { SESSION_COOKIES, workerState, type SessionState } from '../support/worker-session';

export { SESSION_COOKIES };

/** A Playwright storage state as a context takes it (not a file path). */
export type StorageState = Awaited<ReturnType<BrowserContext['storageState']>>;

/**
 * ADR-WEB-02: a signed-in page keeps its access token in memory and its
 * refresh token in an HttpOnly cookie, so a spec cannot read either from
 * storage. It asks the refresh route the way the page does: the browser
 * context's cookie, sent from the page's origin.
 */
const REFRESH_PATH = '/api/v1/userAccount/refresh/token';
// The setup project's state, resolved from this file so a run started outside `frontend/` finds it too.
const SETUP_STATE_FILE = path.resolve(__dirname, '../../../.auth/user.json');

export function apiBaseURL(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:3000';
}

export async function accessTokenFromSession(page: Page): Promise<string> {
  const origin = new URL(page.url()).origin;
  const res = await page.request.post(`${apiBaseURL()}${REFRESH_PATH}`, {
    headers: { origin, 'content-type': 'application/json' },
    data: {},
  });
  if (!res.ok()) throw new Error(`refresh from the session cookie answered ${res.status()}`);
  const body = (await res.json()) as { accessToken?: string };
  if (!body.accessToken) throw new Error('refresh from the session cookie returned no access token');
  return body.accessToken;
}

/** Every value the page keeps in localStorage and sessionStorage, by key. */
export async function webStorage(page: Page): Promise<Record<string, string>> {
  return page.evaluate(() => {
    const out: Record<string, string> = {};
    for (const store of [window.localStorage, window.sessionStorage]) {
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i);
        if (key !== null) out[key] = store.getItem(key) ?? '';
      }
    }
    return out;
  });
}

/** One call that the account's sign-in limiter (10 a minute by default) may answer 429: waits as told, then again. */
async function withinSignInLimit(call: () => Promise<APIResponse>) {
  const deadline = Date.now() + 150_000;
  for (;;) {
    const res = await call();
    if (res.status() !== 429 || Date.now() > deadline) return res;
    const wait = Number(res.headers()['retry-after']);
    await new Promise((r) => setTimeout(r, (Number.isFinite(wait) && wait > 0 ? Math.min(wait, 60) : 10) * 1000));
  }
}

/**
 * A new sign-in (a session family of its own) as *email*, the way the web app signs in: the refresh token comes back
 * as the session cookie, which `api` keeps. Returns that cookie's value; never print it.
 */
export async function signInOn(api: APIRequestContext, email: string, password: string): Promise<string> {
  const origin = new URL(apiBaseURL()).origin;
  const init = await withinSignInLimit(() => api.post('/api/v1/userAccount/initAuth', { data: { email } }));
  if (!init.ok()) throw new Error(`initAuth answered ${init.status()}`);
  const session = init.headers()['x-session-token'];
  const auth = await withinSignInLimit(() =>
    api.post('/api/v1/userAccount/authenticate', {
      headers: { 'x-session-token': session ?? '', 'x-pipeshub-session': 'cookie', origin },
      data: { method: 'password', credentials: { password }, email },
    }),
  );
  if (!auth.ok()) throw new Error(`authenticate answered ${auth.status()}`);
  const cookie = (await api.storageState()).cookies.find((c) => (SESSION_COOKIES as readonly string[]).includes(c.name));
  if (!cookie?.value) throw new Error('sign-in set no session cookie');
  return cookie.value;
}

/** A worker's own sign-in, as a storage state for its tests (support/worker-session.ts). */
export async function signInWorkerSession(): Promise<StorageState> {
  const email = process.env.TEST_USER_EMAIL;
  const password = process.env.TEST_USER_PASSWORD;
  if (!email || !password) throw new Error('TEST_USER_EMAIL and TEST_USER_PASSWORD must be set.');
  const api = await request.newContext({ baseURL: apiBaseURL() });
  try {
    await signInOn(api, email, password);
    const setup = fs.existsSync(SETUP_STATE_FILE)
      ? (JSON.parse(fs.readFileSync(SETUP_STATE_FILE, 'utf-8')) as SessionState)
      : null;
    return workerState(await api.storageState(), setup);
  } finally {
    await api.dispose();
  }
}

/**
 * The state a test's context ended with, after one more refresh with its cookie: that proves the session still
 * refreshes and takes the newest cookie, also when the page's last refresh was cut off before its answer. Null when
 * the session no longer refreshes (the test cleared it or signed out) or the context is gone.
 */
export async function refreshedState(context: BrowserContext, pageOrigin: string): Promise<StorageState | null> {
  try {
    const res = await context.request.post(`${apiBaseURL()}${REFRESH_PATH}`, {
      headers: { origin: pageOrigin, 'content-type': 'application/json' },
      data: {},
      failOnStatusCode: false,
    });
    return res.ok() ? await context.storageState() : null;
  } catch {
    return null;
  }
}

/** The sign-in (session family, `sid`) a refresh token belongs to, read from its payload; null when it has none. */
export function sessionFamily(refreshToken: string): string | null {
  const payload = refreshToken.split('.')[1];
  if (!payload) return null;
  try {
    const claims = JSON.parse(Buffer.from(payload, 'base64url').toString('utf-8')) as { sid?: unknown };
    return typeof claims.sid === 'string' && claims.sid ? claims.sid : null;
  } catch {
    return null;
  }
}

export interface Refreshed {
  status: number;
  /** The session cookie the answer set: a value, '' when it cleared the cookie, null when it set none. */
  cookie: string | null;
  accessToken: boolean;
}

/** One refresh with *cookie* and nothing else (no cookie jar), as a tab of the web app sends it. */
export async function refreshWithCookie(cookie: string): Promise<Refreshed> {
  const base = apiBaseURL();
  // ADR-WEB-02 D2: the __Host- name on https, the plain one on http.
  const name = new URL(base).protocol === 'https:' ? '__Host-pipeshub_refresh' : 'pipeshub_refresh';
  const res = await fetch(`${base}${REFRESH_PATH}`, {
    method: 'POST',
    headers: { cookie: `${name}=${cookie}`, origin: new URL(base).origin, 'content-type': 'application/json' },
    body: '{}',
  });
  let set: string | null = null;
  for (const line of res.headers.getSetCookie()) {
    const [pair] = line.split(';');
    const eq = pair.indexOf('=');
    if (!(SESSION_COOKIES as readonly string[]).includes(pair.slice(0, eq).trim())) continue;
    const value = pair.slice(eq + 1);
    set = value === '' || /max-age=0|1970/i.test(line) ? '' : value;
  }
  const body = (await res.json().catch(() => ({}))) as { accessToken?: string };
  return { status: res.status, cookie: set, accessToken: !!body.accessToken };
}

/** A sign-in of its own as the test user (another device): its session cookie; never print it. */
export async function signInAnotherDevice(): Promise<string> {
  const api = await request.newContext({ baseURL: apiBaseURL() });
  try {
    return await signInOn(api, process.env.TEST_USER_EMAIL!, process.env.TEST_USER_PASSWORD!);
  } finally {
    await api.dispose();
  }
}
