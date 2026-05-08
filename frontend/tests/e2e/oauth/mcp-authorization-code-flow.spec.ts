/**
 * End-to-end OAuth flow similar to how Cursor (or another MCP host) connects:
 * 1. RFC 7591 — register a public client (redirect URI + PKCE).
 * 2. Browser — signed-in user opens the authorization URL; approves consent.
 * 3. RFC 6749 — exchange `authorization_code` + `code_verifier` at `/oauth2/token`.
 * 4. Developer Settings — app appears under OAuth 2.0 (DCR / third-party MCP).
 * 5. UI — disconnect via Manage → Advanced → Revoke/Remove (typed delete).
 *
 */
import crypto from 'crypto';

import { test, expect, E2E_API_BASE_URL } from '../fixtures/api-context.fixture';
import { generatePkceS256 } from '../helpers/pkce.helper';

/** Mirrors `DefaultMcpScopes` in backend oauth_provider config — must stay in sync for authorize. */
const MCP_CONNECT_SCOPE =
  'openid profile email offline_access connector:read connector:write semantic:read semantic:write conversation:read conversation:write conversation:chat';

const OAUTH2_SETTINGS_ROUTE = '/workspace/developer-settings/oauth2/';

test.describe('MCP-style OAuth authorization code + PKCE', () => {
  test('DCR → authorize → token → listed under OAuth 2.0 → delete from UI', async ({
    page,
  }) => {
    const frontendBase =
      process.env.BASE_URL?.trim().replace(/\/$/, '') ||
      process.env.PLAYWRIGHT_BASE_URL?.trim().replace(/\/$/, '') ||
      'http://localhost:3001';

    const appDisplayName = `e2e-mcp-ui-${Date.now()}`;
    const redirectPort = 54321;
    const redirectUri = `http://127.0.0.1:${redirectPort}/oauth/callback`;
    const state = crypto.randomBytes(16).toString('hex');
    const { codeVerifier, codeChallenge } = generatePkceS256();

    const registerRes = await page.request.post(`${E2E_API_BASE_URL}/api/v1/oauth2/register`, {
      data: {
        client_name: appDisplayName,
        redirect_uris: [redirectUri],
        grant_types: ['authorization_code', 'refresh_token'],
        response_types: ['code'],
        token_endpoint_auth_method: 'none',
        application_type: 'native',
      },
    });
    expect(registerRes.status(), await registerRes.text()).toBe(201);

    const registered = (await registerRes.json()) as {
      client_id: string;
      registration_access_token: string;
    };
    const { client_id: clientId, registration_access_token: registrationAccessToken } =
      registered;

    await page.route(`http://127.0.0.1:${redirectPort}/**`, async (route) => {
      await route.fulfill({
        status: 200,
        body: 'ok',
        headers: { 'Content-Type': 'text/plain' },
      });
    });

    let removedViaUi = false;

    try {
      const authorizeUrl = new URL('/oauth/authorize', frontendBase);
      authorizeUrl.searchParams.set('response_type', 'code');
      authorizeUrl.searchParams.set('client_id', clientId);
      authorizeUrl.searchParams.set('redirect_uri', redirectUri);
      authorizeUrl.searchParams.set('scope', MCP_CONNECT_SCOPE);
      authorizeUrl.searchParams.set('state', state);
      authorizeUrl.searchParams.set('code_challenge', codeChallenge);
      authorizeUrl.searchParams.set('code_challenge_method', 'S256');

      await page.goto(authorizeUrl.toString());

      const consentResponsePromise = page.waitForResponse(
        (r) =>
          r.request().method() === 'POST' &&
          r.url().includes('/oauth2/authorize') &&
          r.ok()
      );

      await expect(page.getByRole('button', { name: /^Allow$/i })).toBeVisible({
        timeout: 45_000,
      });
      await page.getByRole('button', { name: /^Allow$/i }).click();

      const consentRes = await consentResponsePromise;
      const consentJson = (await consentRes.json()) as { redirectUrl?: string };
      expect(consentJson.redirectUrl, 'consent POST should return redirectUrl').toBeTruthy();

      const callback = new URL(consentJson.redirectUrl!);
      expect(callback.searchParams.get('state')).toBe(state);
      const code = callback.searchParams.get('code');
      expect(code).toBeTruthy();

      const tokenRes = await page.request.post(`${E2E_API_BASE_URL}/api/v1/oauth2/token`, {
        form: {
          grant_type: 'authorization_code',
          code: code!,
          redirect_uri: redirectUri,
          client_id: clientId,
          code_verifier: codeVerifier,
        },
      });
      expect(tokenRes.ok(), await tokenRes.text()).toBeTruthy();

      const tokens = (await tokenRes.json()) as {
        access_token?: string;
        refresh_token?: string;
        token_type?: string;
      };
      expect(tokens.access_token?.length).toBeGreaterThan(0);
      expect(tokens.refresh_token?.length).toBeGreaterThan(0);
      expect(tokens.token_type?.toLowerCase()).toBe('bearer');

      await page.goto(OAUTH2_SETTINGS_ROUTE);

      await expect(page.getByRole('heading', { level: 1, name: 'OAuth 2.0' })).toBeVisible({
        timeout: 30_000,
      });

      await expect(page.getByText(appDisplayName, { exact: true })).toBeVisible({
        timeout: 30_000,
      });
      await expect(page.getByText('Third-party MCP client').first()).toBeVisible();

      const appCard = page
        .locator('div')
        .filter({ hasText: appDisplayName })
        .filter({ hasText: 'Third-party MCP client' })
        .first();
      await appCard.getByRole('button', { name: /Manage/i }).first().click();

      await expect(page.getByText('Manage OAuth 2.0 Application').first()).toBeVisible({
        timeout: 15_000,
      });

      await page.getByRole('tab', { name: 'Advanced' }).click();

      await page.getByRole('button', { name: /Revoke \/ Remove/i }).click();

      const deleteDialog = page.getByRole('dialog', { name: /Delete OAuth application/i });
      await expect(deleteDialog).toBeVisible();
      await deleteDialog.getByRole('textbox').fill(appDisplayName);

      const deleteWait = page.waitForResponse(
        (r) =>
          r.request().method() === 'DELETE' &&
          r.url().includes('/api/v1/oauth-clients/') &&
          r.ok()
      );
      await deleteDialog.getByRole('button', { name: 'Delete Application' }).click();
      await deleteWait;

      await expect(page.getByText(appDisplayName, { exact: true })).not.toBeVisible({
        timeout: 20_000,
      });

      removedViaUi = true;
    } finally {
      if (!removedViaUi) {
        const del = await page.request.delete(`${E2E_API_BASE_URL}/api/v1/oauth2/register/${clientId}`, {
          headers: { Authorization: `Bearer ${registrationAccessToken}` },
        });
        expect(
          del.status(),
          `RFC 7592 cleanup failed (${del.status()}): ${await del.text()}`
        ).toBe(204);
      }
    }
  });
});
