import { request, type APIRequestContext } from '@playwright/test';
import { test as base } from './base.fixture';
import * as fs from 'fs';

/**
 * Extracts the access token from the saved auth storage state.
 * The auth store persists each token as a plain string under
 * localStorage key "jwt_access_token" / "jwt_refresh_token".
 */
function getAccessToken(): string {
  const raw = fs.readFileSync('.auth/user.json', 'utf-8');
  const storageState = JSON.parse(raw);

  const tokenEntry = storageState.origins
    ?.flatMap((o: { localStorage: { name: string; value: string }[] }) => o.localStorage)
    ?.find((item: { name: string }) => item.name === 'jwt_access_token');

  if (!tokenEntry || !tokenEntry.value) {
    throw new Error('jwt_access_token not found in .auth/user.json — run the setup project first.');
  }

  return tokenEntry.value;
}

type ApiFixtures = {
  apiContext: APIRequestContext;
};

export const test = base.extend<ApiFixtures>({
  apiContext: async ({}, use) => {
    const token = getAccessToken();
    const apiBaseURL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:3000';

    const ctx = await request.newContext({
      baseURL: apiBaseURL,
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
