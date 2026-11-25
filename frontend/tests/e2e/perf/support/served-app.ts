/**
 * The signed-in shell with every API served by the spec (page.route), for the perf project's volume specs: an
 * unsigned token the page reads as signed in, an admin profile, the harness on, and an empty answer for any API a
 * spec does not serve. Shared by perf/volume-*.perf.spec.ts; chat-large-run.perf.spec.ts keeps its own copy.
 */
import type { Page, Route } from '@playwright/test';

export const USER_ID = 'aaaaaaaaaaaaaaaaaaaaaaaa';
export const ORG_ID = 'bbbbbbbbbbbbbbbbbbbbbbbb';

export const json = (route: Route, body: unknown, status = 200): Promise<void> =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

function fakeToken(): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'none', typ: 'JWT' })}.${part({ userId: USER_ID, orgId: ORG_ID, email: 'volume@example.com', exp: 4_102_444_800 })}.sig`;
}

/** The shell's own reads; `flags` are the org's feature flags the page under test needs on. */
export async function serveShell(page: Page, flags: Record<string, boolean> = {}): Promise<void> {
  const token = fakeToken();
  await page.addInitScript(([access]) => {
    window.localStorage.setItem('jwt_access_token', access);
    window.localStorage.setItem('jwt_refresh_token', access);
  }, [token]);
  // Registered first, so every route below (and each spec's own) takes precedence.
  await page.route('**/api/**', (route) => json(route, {}));
  await page.route('**/socket.io/**', (route) => route.abort());
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    json(route, { featureFlags: flags, agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true } }),
  );
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route(`**/api/v1/users/${USER_ID}`, (route) =>
    json(route, { _id: USER_ID, userId: USER_ID, orgId: ORG_ID, fullName: 'Volume Tester', firstName: 'Volume', lastName: 'Tester', email: 'volume@example.com', hasLoggedIn: true, role: 'admin' }),
  );
  await page.route('**/api/v1/conversations*', (route) =>
    json(route, { conversations: [], source: 'owned', pagination: { page: 1, limit: 10, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false } }),
  );
  await page.route('**/api/v1/agent-harness/inbox/count', (route) =>
    json(route, { unread: 0, waiting: 0, badge: 0, paused: false, pausedUntil: null }),
  );
}

/** Every API request the page sends from now on, as `METHOD path` (the query left out, so pages of one list match). */
export function recordApi(page: Page): string[] {
  const seen: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) seen.push(`${request.method()} ${url.pathname}`);
  });
  return seen;
}

/** Each request line with how many times it was sent: what a budget failure prints. */
export function tally(requests: string[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of requests) out[r] = (out[r] ?? 0) + 1;
  return out;
}
