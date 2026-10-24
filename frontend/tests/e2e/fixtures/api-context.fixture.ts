import { request, type APIRequestContext } from '@playwright/test';
import { test as base } from './base.fixture';
import * as fs from 'fs';
import * as path from 'path';

// Resolved from this file, not the working directory, so the fixture also
// works when Playwright is started from outside `frontend/`.
const AUTH_FILE = path.resolve(__dirname, '../../../.auth/user.json');

/**
 * Extracts the access token from the saved auth storage state.
 * The auth store persists each token as a plain string under localStorage key
 * "jwt_access_token" / "jwt_refresh_token"; a store that keeps its token
 * pairs in a JSON map persists them under "pipeshub_org_tokens".
 */
export function getAccessToken(): string {
  const raw = fs.readFileSync(AUTH_FILE, 'utf-8');
  const storageState = JSON.parse(raw);

  const items: { name: string; value: string }[] =
    storageState.origins?.flatMap((o: { localStorage: { name: string; value: string }[] }) => o.localStorage) ?? [];
  const tokenEntry = items.find((item) => item.name === 'jwt_access_token');
  if (tokenEntry?.value) {
    return tokenEntry.value;
  }

  const orgTokens = items.find((item) => item.name === 'pipeshub_org_tokens');
  if (orgTokens?.value) {
    const pairs = Object.values(JSON.parse(orgTokens.value) as Record<string, { accessToken?: string }>);
    // The active entry lives in sessionStorage, which storage state does not keep.
    if (pairs.length !== 1 || !pairs[0].accessToken) {
      throw new Error(`.auth/user.json holds ${pairs.length} token pairs; the e2e user must have exactly one.`);
    }
    return pairs[0].accessToken;
  }

  throw new Error('jwt_access_token not found in .auth/user.json — run the setup project first.');
}

export function apiBaseURL(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:3000';
}

type ApiFixtures = {
  apiContext: APIRequestContext;
};

export const test = base.extend<ApiFixtures>({
  apiContext: async ({}, use) => {
    const token = getAccessToken();

    const ctx = await request.newContext({
      baseURL: apiBaseURL(),
      extraHTTPHeaders: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    });

    await use(ctx);
    await ctx.dispose();
  },
});

export { expect } from './base.fixture';
