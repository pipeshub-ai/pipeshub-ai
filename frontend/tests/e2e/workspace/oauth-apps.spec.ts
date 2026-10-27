/**
 * Developer OAuth apps, end to end: Workspace → Developer settings → OAuth 2.0.
 *
 * Nothing is mocked. The apps made here use the authorization-code grant (the
 * form's default; client credentials would also need a service account). To
 * prove the credentials the UI shows are the real ones, the test plays the
 * user's part of that flow through the API: it approves the app with its own
 * session (`POST /api/v1/oauth2/authorize`), then trades the code for a token
 * at `POST /api/v1/oauth2/token` with the client ID and secret read from the
 * page, and calls `GET /api/v1/users` with that token. The token endpoint
 * checks the secret before it looks at the code, so a wrong secret is refused
 * with `invalid_client` and leaves the code usable for the right one.
 *
 * Secrets and tokens are compared as booleans rather than with `toBe`, so a
 * failure report never prints a working credential.
 */
import { request, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { test, expect, apiBaseURL } from '../fixtures/api-context.fixture';

const PAGE = '/workspace/developer-settings/oauth2/';
const OAUTH_API = '/api/v1/oauth-clients';
const REDIRECT_URI = 'https://e2e.example.com/oauth/callback';
const SCOPE = 'user:read';
const IN_SCOPE_ROUTE = '/api/v1/users';

type CreatedApp = { id: string; clientId: string; clientSecret: string };
type TokenResult = { status: number; body: { access_token?: string; error?: string; error_description?: string } };

/** Approve the app as the signed-in user, the way the consent screen does, and return the code. */
async function authorizationCode(apiContext: APIRequestContext, clientId: string): Promise<string> {
  const res = await apiContext.post('/api/v1/oauth2/authorize', {
    data: {
      client_id: clientId,
      redirect_uri: REDIRECT_URI,
      scope: SCOPE,
      state: `e2e-${Date.now()}`,
      consent: 'granted',
    },
  });
  const body = (await res.json()) as { redirectUrl?: string; error?: string; error_description?: string };
  expect(res.status(), `approving the app failed: ${body.error ?? ''} ${body.error_description ?? ''}`).toBe(200);
  const code = new URL(body.redirectUrl ?? 'about:blank').searchParams.get('code');
  expect(code, 'approving the app returned no authorization code').toBeTruthy();
  return code!;
}

/** Trade a code for tokens as the app would, with no session of its own. */
async function exchangeCode(code: string, clientId: string, clientSecret: string): Promise<TokenResult> {
  const ctx = await request.newContext({ baseURL: apiBaseURL() });
  try {
    const res = await ctx.post('/api/v1/oauth2/token', {
      form: {
        grant_type: 'authorization_code',
        code,
        redirect_uri: REDIRECT_URI,
        client_id: clientId,
        client_secret: clientSecret,
      },
    });
    return { status: res.status(), body: (await res.json()) as TokenResult['body'] };
  } finally {
    await ctx.dispose();
  }
}

async function statusWithToken(token: string, path: string): Promise<number> {
  const ctx = await request.newContext({
    baseURL: apiBaseURL(),
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
  try {
    return (await ctx.get(path)).status();
  } finally {
    await ctx.dispose();
  }
}

async function deleteApp(apiContext: APIRequestContext, appId: string): Promise<void> {
  // 404 when the test already deleted it.
  const removed = await apiContext.delete(`${OAUTH_API}/${appId}`);
  expect([200, 404], `deleting the test app failed: ${removed.status()}`).toContain(removed.status());
}

async function openCreatePanel(page: Page): Promise<Locator> {
  await page.goto(PAGE);
  // The empty state repeats the header's button; the header's comes first.
  await page.getByRole('button', { name: 'New OAuth application' }).first().click();
  const panel = page.getByRole('dialog', { name: 'Create OAuth 2.0 application' });
  await expect(panel).toBeVisible();
  return panel;
}

/** The checkbox on one scope's row; the row has no label of its own. */
function scopeCheckbox(panel: Locator, scope: string): Locator {
  const page = panel.page();
  return panel
    .locator('div')
    .filter({ has: page.getByText(scope, { exact: true }) })
    .filter({ has: page.getByRole('checkbox') })
    .last()
    .getByRole('checkbox');
}

/** An app's card: the innermost block holding its name and its Manage button. */
function appCard(page: Page, name: string): Locator {
  return page
    .locator('div')
    .filter({ has: page.getByText(name, { exact: true }) })
    .filter({ has: page.getByRole('button', { name: 'Manage' }) })
    .last();
}

async function credentialOnPage(page: Page, value: string): Promise<boolean> {
  return page.evaluate(
    (v) =>
      document.body.innerText.includes(v) ||
      Array.from(document.querySelectorAll('input')).some((input) => input.value === v),
    value,
  );
}

test.describe('Developer OAuth apps', () => {
  test('an invalid redirect URI is refused with its message', async ({ page }) => {
    const panel = await openCreatePanel(page);
    await panel.getByPlaceholder('My application').fill(`e2e-oauth-validation-${Date.now()}`);
    const next = panel.getByRole('button', { name: 'Next' });
    const uris = panel.getByPlaceholder('https://yourapp.com/callback');

    // Plain http is allowed only for localhost.
    await uris.first().fill('http://e2e.example.com/oauth/callback');
    await expect(next, 'Next should stay off while no redirect URI is valid').toBeDisabled();

    // With one good URI beside it, Next checks every URI and names the problem.
    await panel.getByRole('button', { name: 'Add redirect URI' }).click();
    await uris.nth(1).fill(REDIRECT_URI);
    await expect(next).toBeEnabled();
    await next.click();
    await expect(panel.getByText('Each redirect URI must be a valid http(s) URL.')).toBeVisible();
    await expect(panel.getByRole('tab', { name: 'General' })).toHaveAttribute('aria-selected', 'true');

    await panel.getByRole('button', { name: 'Remove redirect URI' }).first().click();
    await expect(uris).toHaveCount(1);
    await expect(uris.first()).toHaveValue(REDIRECT_URI);
    await next.click();
    await expect(panel.getByRole('tab', { name: 'Permissions & scopes' })).toHaveAttribute('aria-selected', 'true');

    await panel.getByRole('button', { name: 'Cancel' }).click();
    await expect(panel).toBeHidden();
  });

  test('a new app shows its client ID and one-time secret, and they work at the token endpoint', async ({
    page,
    apiContext,
  }) => {
    test.setTimeout(120_000);
    const name = `e2e-oauth-${Date.now()}`;
    let appId: string | undefined;

    try {
      const panel = await openCreatePanel(page);
      await panel.getByPlaceholder('My application').fill(name);
      await panel.getByPlaceholder('https://yourapp.com/callback').fill(REDIRECT_URI);
      await panel.getByRole('button', { name: 'Next' }).click();

      await expect(panel.getByRole('tab', { name: 'Permissions & scopes' })).toHaveAttribute('aria-selected', 'true');
      const scopeBox = scopeCheckbox(panel, SCOPE);
      await expect(scopeBox).toBeVisible({ timeout: 15_000 });
      await expect(panel.getByText('Select at least one permission scope.', { exact: true })).toBeVisible();
      await expect(panel.getByRole('button', { name: 'Next' })).toBeDisabled();
      await scopeBox.click();
      await expect(scopeBox).toBeChecked();
      await expect(panel.getByText(/^1 of \d+ permissions selected$/)).toBeVisible();
      await panel.getByRole('button', { name: 'Next' }).click();

      await expect(panel.getByRole('tab', { name: 'Granular permissions' })).toHaveAttribute('aria-selected', 'true');
      const created = page.waitForResponse(
        (r) =>
          r.request().method() === 'POST' && new URL(r.url()).pathname.replace(/\/$/, '') === OAUTH_API,
      );
      await panel.getByRole('button', { name: 'Create app' }).click();
      const response = await created;
      const body = (await response.json()) as { app?: Partial<CreatedApp>; error?: { message?: string } };
      expect(response.status(), `creating the app failed: ${body.error?.message ?? ''}`).toBe(201);
      appId = body.app?.id;
      const clientId = body.app?.clientId ?? '';
      expect(appId, 'the create response carried no app id').toBeTruthy();
      expect(clientId).toBeTruthy();

      const success = page.getByRole('dialog', { name: 'OAuth application created' });
      await expect(success).toBeVisible();
      await expect(
        success.getByText('Store the client secret securely. It will not be displayed again.'),
      ).toBeVisible();
      const fields = success.locator('input[readonly]');
      await expect(fields).toHaveCount(2);
      await expect(fields.nth(0), 'the client ID should be shown').toHaveValue(clientId);
      await expect(fields.nth(1), 'the secret should start hidden').toHaveAttribute('type', 'password');
      await success.getByRole('button', { name: 'Show client secret' }).click();
      await expect(fields.nth(1)).toHaveAttribute('type', 'text');
      const secret = await fields.nth(1).inputValue();
      expect(
        secret.length > 0 && secret === body.app?.clientSecret,
        'the page should show the secret the API issued',
      ).toBe(true);

      const code = await authorizationCode(apiContext, clientId);
      const withWrongSecret = await exchangeCode(code, clientId, `${secret}-wrong`);
      expect(withWrongSecret.status, 'a wrong secret should be refused').toBe(401);
      expect(withWrongSecret.body.error).toBe('invalid_client');
      const issued = await exchangeCode(code, clientId, secret);
      expect(
        issued.status,
        `the shown credentials should get a token: ${issued.body.error ?? ''} ${issued.body.error_description ?? ''}`,
      ).toBe(200);
      expect(issued.body.access_token, 'the token response carried no access token').toBeTruthy();
      expect(
        await statusWithToken(issued.body.access_token!, IN_SCOPE_ROUTE),
        `the app's token should be accepted on ${IN_SCOPE_ROUTE}`,
      ).toBe(200);

      await success.getByRole('button', { name: 'Return to OAuth 2.0 apps' }).click();
      await expect(success).toBeHidden();
      // Newest first, so a new app is on the first page.
      await expect(appCard(page, name), 'the new app should be listed').toBeVisible({ timeout: 15_000 });
      expect(await credentialOnPage(page, secret), 'the secret must not be shown again once the panel is closed').toBe(
        false,
      );
    } finally {
      if (appId) await deleteApp(apiContext, appId);
    }
  });

  test('an app can be edited, given a new secret and deleted from its manage panel', async ({ page, apiContext }) => {
    test.setTimeout(120_000);
    const name = `e2e-oauth-manage-${Date.now()}`;
    const newDescription = `Edited by e2e ${Date.now()}`;
    let app: CreatedApp | undefined;

    try {
      const seeded = await apiContext.post(OAUTH_API, {
        data: {
          name,
          redirectUris: [REDIRECT_URI],
          allowedGrantTypes: ['authorization_code', 'refresh_token'],
          allowedScopes: [SCOPE],
          isConfidential: true,
        },
      });
      expect(seeded.status(), `creating the app to manage failed: ${await seeded.text()}`).toBe(201);
      app = ((await seeded.json()) as { app: CreatedApp }).app;

      await page.goto(PAGE);
      // Search rather than scan: earlier apps can push this one off the first page.
      await page.getByPlaceholder('Search...').fill(name);
      const card = appCard(page, name);
      await expect(card).toBeVisible({ timeout: 15_000 });
      await card.getByRole('button', { name: 'Manage' }).click();

      const panel = page.getByRole('dialog', { name: 'Manage OAuth 2.0 application' });
      const description = panel.getByPlaceholder('A brief description of this application');
      await expect(description).toBeVisible({ timeout: 15_000 });
      const save = panel.getByRole('button', { name: 'Save changes' });
      await expect(save, 'Save should stay off until something changes').toBeDisabled();
      await description.fill(newDescription);
      await expect(save).toBeEnabled();
      await save.click();
      await expect(page.getByText('Changes saved', { exact: true }).first()).toBeVisible({ timeout: 15_000 });
      await expect(panel).toBeHidden();

      await page.reload();
      await page.getByPlaceholder('Search...').fill(name);
      await expect(card, 'the card should show the saved description after a reload').toContainText(newDescription, {
        timeout: 15_000,
      });
      await card.getByRole('button', { name: 'Manage' }).click();
      await expect(description, 'the saved description should persist after a reload').toHaveValue(newDescription, {
        timeout: 15_000,
      });

      const regenerated = page.waitForResponse(
        (r) =>
          r.request().method() === 'POST' &&
          new URL(r.url()).pathname === `${OAUTH_API}/${app!.id}/regenerate-secret`,
      );
      await panel.getByRole('button', { name: 'Generate new client secret' }).click();
      const regenResponse = await regenerated;
      expect(regenResponse.status(), 'regenerating the secret failed').toBe(200);
      const regenBody = (await regenResponse.json()) as { clientSecret?: string };
      await expect(page.getByText('Secret regenerated', { exact: true }).first()).toBeVisible({ timeout: 15_000 });
      await expect(panel.getByText('New client secret', { exact: true })).toBeVisible();
      // The panel's read-only fields are the client ID and, once made, the new secret.
      const secretFields = panel.locator('input[readonly]');
      await expect(secretFields).toHaveCount(2);
      const newSecret = await secretFields.nth(1).inputValue();
      expect(
        newSecret.length > 0 && newSecret === regenBody.clientSecret,
        'the panel should show the secret the API issued',
      ).toBe(true);
      expect(newSecret !== app.clientSecret, 'the new secret should differ from the old one').toBe(true);

      const code = await authorizationCode(apiContext, app.clientId);
      const withOldSecret = await exchangeCode(code, app.clientId, app.clientSecret);
      expect(withOldSecret.status, 'the old secret should stop working').toBe(401);
      expect(withOldSecret.body.error).toBe('invalid_client');
      const issued = await exchangeCode(code, app.clientId, newSecret);
      expect(
        issued.status,
        `the new secret should get a token: ${issued.body.error ?? ''} ${issued.body.error_description ?? ''}`,
      ).toBe(200);
      const accessToken = issued.body.access_token ?? '';
      expect(await statusWithToken(accessToken, IN_SCOPE_ROUTE)).toBe(200);

      await panel.getByRole('tab', { name: 'Advanced' }).click();
      await panel.getByRole('button', { name: 'Delete application' }).click();
      const confirm = page.getByRole('dialog', { name: 'Delete OAuth application' });
      await expect(confirm).toContainText(`Are you sure you want to delete ${name}?`);
      const confirmDelete = confirm.getByRole('button', { name: 'Delete application' });
      await expect(confirmDelete, 'deleting should wait for the app name to be typed').toBeDisabled();
      await confirm.getByRole('textbox').fill(name);
      await expect(confirmDelete).toBeEnabled();
      await confirmDelete.click();
      await expect(page.getByText('Application deleted', { exact: true }).first()).toBeVisible({ timeout: 15_000 });
      await expect(panel).toBeHidden();
      await expect(card, 'the deleted app should leave the list').toHaveCount(0, { timeout: 15_000 });
      await expect(page.getByText('No applications match your search.')).toBeVisible();

      const gone = await apiContext.get(`${OAUTH_API}/${app.id}`);
      expect(gone.status(), 'the API should no longer find the deleted app').toBe(404);
      expect(await statusWithToken(accessToken, IN_SCOPE_ROUTE), "a deleted app's tokens should be refused").toBe(401);
      const afterDelete = await exchangeCode('unused-code', app.clientId, newSecret);
      expect(afterDelete.status, "a deleted app's credentials should be refused").toBe(401);
      expect(afterDelete.body.error).toBe('invalid_client');
      expect(await credentialOnPage(page, newSecret), 'the new secret must not stay on the page').toBe(false);
    } finally {
      if (app) await deleteApp(apiContext, app.id);
    }
  });
});
