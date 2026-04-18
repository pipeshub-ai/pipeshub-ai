import { request, type APIRequestContext } from '@playwright/test';
import { test as base } from './base.fixture';
import * as fs from 'fs';

/**
 * Extracts the access token from the saved auth storage state.
 * The auth store persists to localStorage under key "auth-storage"
 * with shape: { state: { accessToken, refreshToken }, version: 0 }
 */
function getAccessToken(): string {
  const raw = fs.readFileSync('.auth/user.json', 'utf-8');
  const storageState = JSON.parse(raw);

  const authEntry = storageState.origins
    ?.flatMap((o: { localStorage: { name: string; value: string }[] }) => o.localStorage)
    ?.find((item: { name: string }) => item.name === 'auth-storage');

  if (!authEntry) {
    throw new Error('auth-storage not found in .auth/user.json — run the setup project first.');
  }

  const parsed = JSON.parse(authEntry.value);
  const token = parsed?.state?.accessToken;

  if (!token) {
    throw new Error('accessToken missing in auth-storage — login may have failed.');
  }

  return token;
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
