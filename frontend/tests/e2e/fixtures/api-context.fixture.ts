import { request, type APIRequestContext } from '@playwright/test';
import { test as base } from './base.fixture';
import { cancelLeftRuns, hasLeftTurns } from '../harness/support/lane';
import * as fs from 'fs';
import * as path from 'path';

// Resolved from this file, not the working directory, so the fixture also
// works when Playwright is started from outside `frontend/`.
const AUTH_FILE = path.resolve(__dirname, '../../../.auth/user.json');

const API_TOKEN_FILE = path.resolve(__dirname, '../../../.auth/api-token.json');

/**
 * The access token the setup project minted from its session cookie
 * (`.auth/api-token.json`). The page itself keeps its token in memory only
 * (ADR-WEB-02); a state saved by an older build still has it in localStorage.
 */
export function getAccessToken(): string {
  if (fs.existsSync(API_TOKEN_FILE)) {
    const saved = JSON.parse(fs.readFileSync(API_TOKEN_FILE, 'utf-8')) as { accessToken?: string };
    if (saved.accessToken) return saved.accessToken;
  }
  const storageState = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'));
  const items: { name: string; value: string }[] =
    storageState.origins?.flatMap((o: { localStorage: { name: string; value: string }[] }) => o.localStorage) ?? [];
  const legacy = items.find((item) => item.name === 'jwt_access_token');
  if (legacy?.value) return legacy.value;
  throw new Error('No API token in .auth/ — run the setup project first.');
}

export function apiBaseURL(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:3000';
}

type ApiFixtures = {
  apiContext: APIRequestContext;
  leftRunsCancelled: void;
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
  leftRunsCancelled: [
    async ({}, use) => {
      await use();
      if (!hasLeftTurns()) return;
      const ctx = await request.newContext({ baseURL: apiBaseURL(), extraHTTPHeaders: { Authorization: `Bearer ${getAccessToken()}` } });
      try {
        await cancelLeftRuns(ctx);
      } finally {
        await ctx.dispose();
      }
    },
    { auto: true },
  ],
});

export { expect } from './base.fixture';
