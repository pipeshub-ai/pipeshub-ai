import { test as setup, expect } from '../fixtures/base.fixture';
import { reachPasswordStep } from '../helpers/login.helper';
import { accessTokenFromSession } from '../helpers/session.helper';
import * as fs from 'fs';

const AUTH_FILE = '.auth/user.json';
// The API fixture's bearer token: the page keeps its own in memory only (ADR-WEB-02).
const API_TOKEN_FILE = '.auth/api-token.json';

// Tagged @smoke so the smoke run (which filters by title) still signs in first.
setup('authenticate @smoke', async ({ page }) => {
  const email = process.env.TEST_USER_EMAIL;
  const password = process.env.TEST_USER_PASSWORD;

  if (!email || !password) {
    throw new Error(
      'TEST_USER_EMAIL and TEST_USER_PASSWORD must be set. ' +
      'Copy .env.test.example to .env.test and fill in credentials.'
    );
  }

  await page.goto('/login/');

  // Wait for the login form to render (it depends on an initAuth API call)
  await page.waitForSelector('#email-field', { timeout: 15_000 });

  await reachPasswordStep(page, email);
  await page.fill('#password-field', password);
  await page.click('button[type="submit"]');

  // Wait for redirect to /chat after successful login
  await page.waitForURL('**/chat/**', { timeout: 15_000 });
  await expect(page).toHaveURL(/\/chat\//);

  // The session is the HttpOnly refresh cookie, which storage state keeps.
  await page.context().storageState({ path: AUTH_FILE });
  fs.writeFileSync(API_TOKEN_FILE, JSON.stringify({ accessToken: await accessTokenFromSession(page) }));
});
