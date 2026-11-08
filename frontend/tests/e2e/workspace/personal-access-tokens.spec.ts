/**
 * Personal access tokens, end to end: Workspace → Developer settings →
 * Personal access tokens.
 *
 * Nothing is mocked. A token is made in the UI, used against the real API,
 * and revoked in the UI, and the API is asked each time whether it accepts it.
 * The token is limited to `user:read`, which `GET /api/v1/users` needs; a
 * route that needs another scope (`GET /api/v1/userGroups`, `usergroup:read`)
 * must refuse it.
 *
 * Token values are compared as booleans rather than with `toBe`, so a failure
 * report never prints a working token.
 */
import { request, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { test, expect, apiBaseURL } from '../fixtures/api-context.fixture';

const PAGE = '/workspace/developer-settings/personal-access-tokens/';
const PAT_API = '/api/v1/personal-access-tokens';
const SCOPE = 'user:read';
const IN_SCOPE_ROUTE = '/api/v1/users';
const OUT_OF_SCOPE_ROUTE = '/api/v1/userGroups';
const DAY_MS = 24 * 60 * 60 * 1000;

type ListedToken = { id: string; name: string; expiresAt: string; lastUsedAt?: string };

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

async function listTokens(apiContext: APIRequestContext): Promise<ListedToken[]> {
  const res = await apiContext.get(PAT_API);
  expect(res.ok(), `listing tokens failed: ${res.status()} ${await res.text()}`).toBe(true);
  return ((await res.json()) as { tokens?: ListedToken[] }).tokens ?? [];
}

async function openCreatePanel(page: Page): Promise<Locator> {
  await page.goto(PAGE);
  // The empty state repeats the header's button; the header's comes first.
  await page.getByRole('button', { name: 'New token' }).first().click();
  const panel = page.getByRole('dialog', { name: 'New personal access token' });
  await expect(panel).toBeVisible();
  // Every scope starts selected, so "Clear all" means the scopes have loaded.
  await expect(panel.getByRole('button', { name: 'Clear all' })).toBeEnabled({ timeout: 15_000 });
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

/** A token's row in the list: the innermost block holding its name and its revoke button. */
function tokenRow(page: Page, name: string): Locator {
  return page
    .locator('div')
    .filter({ hasText: name })
    .filter({ has: page.getByRole('button', { name: `Revoke ${name}` }) })
    .last();
}

test.describe('Personal access tokens', () => {
  test('a token needs a name and at least one scope', async ({ page }) => {
    const panel = await openCreatePanel(page);
    const create = panel.getByRole('button', { name: 'Create token' });
    const nameField = panel.getByPlaceholder('e.g. MCP server');

    await expect(create, 'a token with no name should not be creatable').toBeDisabled();
    await nameField.fill('   ');
    await expect(create, 'a name of only spaces should not count').toBeDisabled();
    await nameField.fill(`e2e-pat-validation-${Date.now()}`);
    await expect(create).toBeEnabled();

    await panel.getByRole('button', { name: 'Clear all' }).click();
    await expect(panel.getByText('Select at least one scope.', { exact: true })).toBeVisible();
    await expect(create, 'a token with no scopes should not be creatable').toBeDisabled();

    await panel.getByRole('button', { name: 'Cancel' }).click();
    await expect(panel).toBeHidden();
  });

  test('a token is shown once, works against the API within its scope, and stops working once revoked', async ({
    page,
    apiContext,
  }) => {
    test.setTimeout(120_000);
    const name = `e2e-pat-${Date.now()}`;
    let tokenId: string | undefined;

    try {
      const panel = await openCreatePanel(page);
      await panel.getByPlaceholder('e.g. MCP server').fill(name);
      await panel.getByRole('radio', { name: '90 days' }).check();
      await panel.getByRole('button', { name: 'Clear all' }).click();
      const scopeBox = scopeCheckbox(panel, SCOPE);
      await scopeBox.click();
      await expect(scopeBox).toBeChecked();

      const created = page.waitForResponse(
        (r) =>
          r.request().method() === 'POST' && new URL(r.url()).pathname.replace(/\/$/, '') === PAT_API,
      );
      await panel.getByRole('button', { name: 'Create token' }).click();
      const response = await created;
      const body = (await response.json()) as {
        token?: { id?: string; scopes?: string[]; accessToken?: string };
      };
      expect(response.status(), 'creating the token failed').toBe(201);
      tokenId = body.token?.id;
      expect(tokenId, 'the create response carried no token id').toBeTruthy();
      expect(body.token?.scopes).toEqual([SCOPE]);

      // The panel turns into the one-time success screen.
      const success = page.getByRole('dialog', { name: 'Token created' });
      await expect(success).toBeVisible();
      await expect(success.getByText('This token is shown only once. Store it somewhere safe.', { exact: true })).toBeVisible();
      const tokenField = success.locator('input[readonly]');
      await expect(tokenField, 'the token should start hidden').toHaveAttribute('type', 'password');
      await success.getByRole('button', { name: 'Show token' }).click();
      await expect(tokenField).toHaveAttribute('type', 'text');
      const token = await tokenField.inputValue();
      expect(token === body.token?.accessToken, 'the page should show the token the API issued').toBe(true);
      expect(token.startsWith('phpat_'), 'a personal access token carries the phpat_ prefix').toBe(true);
      const pasteBlock = await success.getByText('PIPESHUB_MCP_TOKEN=').textContent();
      expect(
        pasteBlock?.includes(`PIPESHUB_MCP_TOKEN=${token}`),
        'the MCP paste block should carry the same token',
      ).toBe(true);

      expect(await statusWithToken(token, IN_SCOPE_ROUTE), `the token should be accepted on ${IN_SCOPE_ROUTE}`).toBe(
        200,
      );
      expect(
        await statusWithToken(token, OUT_OF_SCOPE_ROUTE),
        `a ${SCOPE} token should be refused on ${OUT_OF_SCOPE_ROUTE}`,
      ).toBe(403);

      await success.getByRole('button', { name: 'Done' }).click();
      await expect(success).toBeHidden();
      const tokenStillOnPage = await page.evaluate(
        (value) =>
          document.body.innerText.includes(value) ||
          Array.from(document.querySelectorAll('input')).some((input) => input.value === value),
        token,
      );
      expect(tokenStillOnPage, 'the token must not be shown again once the panel is closed').toBe(false);

      // Opening the panel again starts a new token; it does not bring the old one back.
      await page.getByRole('button', { name: 'New token' }).first().click();
      const reopened = page.getByRole('dialog', { name: 'New personal access token' });
      await expect(reopened).toBeVisible();
      await expect(reopened.getByPlaceholder('e.g. MCP server')).toHaveValue('');
      await expect(reopened.locator('input[readonly]')).toHaveCount(0);
      await reopened.getByRole('button', { name: 'Cancel' }).click();
      await expect(reopened).toBeHidden();

      // The expiry picked in the form is the one stored.
      const listed = (await listTokens(apiContext)).find((t) => t.id === tokenId);
      expect(listed, 'the new token should be in the API list').toBeTruthy();
      const daysLeft = (new Date(listed!.expiresAt).getTime() - Date.now()) / DAY_MS;
      expect(daysLeft, 'a 90-day token should expire about 90 days from now').toBeGreaterThan(89);
      expect(daysLeft).toBeLessThan(91);

      // Last-used is recorded in the background after the request it rides on.
      await expect
        .poll(async () => Boolean((await listTokens(apiContext)).find((t) => t.id === tokenId)?.lastUsedAt), {
          message: 'using the token should record when it was last used',
          timeout: 15_000,
        })
        .toBe(true);

      await page.reload();
      const row = tokenRow(page, name);
      await expect(row, 'the new token should be listed').toBeVisible({ timeout: 15_000 });
      await expect(row).toContainText(SCOPE);
      // Expiry is a date and last-used is now a date too, so "Never" appears nowhere in the row.
      await expect(row, 'the row should show a last-used date, not "Never"').not.toContainText('Never');

      await page.getByRole('button', { name: `Revoke ${name}` }).click();
      const confirm = page.getByRole('alertdialog', { name: 'Revoke token?' });
      await expect(confirm).toContainText(`Are you sure you want to revoke "${name}"?`);
      await confirm.getByRole('button', { name: 'Revoke' }).click();
      await expect(page.getByText('Token revoked', { exact: true }).first()).toBeVisible({ timeout: 15_000 });
      await expect(confirm).toBeHidden();
      await expect(page.getByRole('button', { name: `Revoke ${name}` }), 'the revoked token should leave the list').toHaveCount(
        0,
        { timeout: 15_000 },
      );

      expect(await statusWithToken(token, IN_SCOPE_ROUTE), 'a revoked token should be refused').toBe(401);
      expect(
        (await listTokens(apiContext)).some((t) => t.id === tokenId),
        'the API should no longer list the revoked token',
      ).toBe(false);
    } finally {
      if (tokenId) {
        // 404 when the test already revoked it.
        const removed = await apiContext.delete(`${PAT_API}/${tokenId}`);
        expect([200, 404], `revoking the test token failed: ${removed.status()}`).toContain(removed.status());
      }
    }
  });
});
