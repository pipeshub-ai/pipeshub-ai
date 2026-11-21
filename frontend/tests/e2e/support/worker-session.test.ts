import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { hasSessionCookie, isSharedSignIn, WorkerSession, workerState, type SessionState } from './worker-session';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const E2E = path.resolve(HERE, '..');

const state = (value: string, hint = 'admin@x'): SessionState => ({
  cookies: [{ name: 'pipeshub_refresh', value }],
  origins: [{ origin: 'http://127.0.0.1:3000', localStorage: [{ name: 'pipeshub_signed_in', value: hint }] }],
});

function filesUnder(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : filesUnder(full);
    return /\.(spec|setup|provision|cleanup|guard)\.ts$/.test(entry.name) ? [full] : [];
  });
}

describe('R46-LANE worker sessions (ADR-WEB-02 D9)', () => {
  it('R46LANE-1 only the setup project’s shared file is swapped for the worker’s session', () => {
    expect(isSharedSignIn('.auth/user.json')).toBe(true);
    expect(isSharedSignIn('/work/frontend/.auth/user.json')).toBe(true);
    expect(isSharedSignIn(String.raw`C:\w\.auth\user.json`)).toBe(true);
    expect(isSharedSignIn({ cookies: [], origins: [] })).toBe(false);
    expect(isSharedSignIn('.auth/other.json')).toBe(false);
    expect(isSharedSignIn(undefined)).toBe(false);
  });

  it('R46LANE-2 a worker signs in once and lends each test the cookie its previous test ended with', async () => {
    let signIns = 0;
    const session = new WorkerSession(async () => state(`c${(signIns += 1)}`));
    const first = await session.lend();
    expect(first.cookies[0].value).toBe('c1');
    expect(session.owns(first)).toBe(true);
    session.settle(state('c1-rotated'));
    const second = await session.lend();
    expect(second.cookies[0].value).toBe('c1-rotated');
    expect(session.owns(first)).toBe(false);
    expect(session.signIns).toBe(1);
  });

  it('R46LANE-3 a test that ends its session (no cookie, or the refresh refused) makes the next one sign in again', async () => {
    let signIns = 0;
    const session = new WorkerSession(async () => state(`c${(signIns += 1)}`));
    await session.lend();
    session.settle({ cookies: [], origins: [] });
    expect((await session.lend()).cookies[0].value).toBe('c2');
    session.settle(null);
    expect((await session.lend()).cookies[0].value).toBe('c3');
    expect(session.signIns).toBe(3);
  });

  it('R46LANE-4 a sign-in without a session cookie is refused, not lent', async () => {
    const session = new WorkerSession(async () => ({ cookies: [], origins: [] }));
    await expect(session.lend()).rejects.toThrow(/no session cookie/);
    expect(hasSessionCookie({ cookies: [{ name: 'pipeshub_refresh', value: '' }], origins: [] })).toBe(false);
  });

  it('R46LANE-5 the worker state keeps the setup’s page storage (the signed-in hint) and drops legacy tokens', () => {
    const setup: SessionState = {
      cookies: [{ name: 'pipeshub_refresh', value: 'setup-cookie' }],
      origins: [{ origin: 'http://h', localStorage: [{ name: 'pipeshub_signed_in', value: 'a' }, { name: 'jwt_refresh_token', value: 'old' }] }],
    };
    const out = workerState({ cookies: [{ name: 'pipeshub_refresh', value: 'mine' }, { name: 'other', value: 'x' }] }, setup);
    expect(out.cookies).toEqual([{ name: 'pipeshub_refresh', value: 'mine' }]);
    expect(out.origins[0].localStorage).toEqual([{ name: 'pipeshub_signed_in', value: 'a' }]);
  });

  it('R46LANE-6 no spec takes `test` from @playwright/test, which would start it on the shared, stale state', () => {
    // perf/ serves every API and its own sign-in (no saved state), so it may use Playwright's own test.
    const bypassing = filesUnder(E2E)
      .filter((file) => !file.includes(`${path.sep}perf${path.sep}`))
      .filter((file) => /import\s*\{[^}]*\btest\b[^}]*\}\s*from\s*'@playwright\/test'/.test(readFileSync(file, 'utf-8')))
      .map((file) => path.relative(E2E, file));
    expect(bypassing).toEqual([]);
  });
});
