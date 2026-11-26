import type { APIRequestContext, Page, Response } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { postWithRetry } from '../helpers/api-retry.helper';
import { getRows } from '../helpers/entity-table.helper';
import { changeLimit } from '../helpers/pagination.helper';

const TEAMS_LIST_PATH = '/api/v1/teams/user/teams';

/** The page's own list request for this search text; `null` is the request with no search at all. */
function listAnswered(page: Page, search: string | null): Promise<Response> {
  return page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === TEAMS_LIST_PATH &&
      response.request().method() === 'GET' &&
      url.searchParams.get('search') === search
    );
  });
}

async function createTeam(apiContext: APIRequestContext, name: string): Promise<string> {
  const response = await postWithRetry(apiContext, '/api/v1/teams', { name });
  if (!response.ok()) {
    throw new Error(`POST /api/v1/teams failed [${response.status()}]: ${await response.text()}`);
  }
  const data = await response.json();
  const id: string | undefined = data.data?.id ?? data.data?._key ?? data.id ?? data._key;
  if (!id) throw new Error(`POST /api/v1/teams returned no id: ${JSON.stringify(data)}`);
  return id;
}

test.describe('Teams Table', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/teams/');
    await page.waitForTimeout(3_000);
  });

  test('page loads with teams table', async ({ page }) => {
    const heading = page.locator('text=/Teams/i').first();
    await expect(heading).toBeVisible({ timeout: 5_000 });
  });

  test('displays pagination', async ({ page }) => {
    const showing = page.locator('text=/Showing/i').first();
    await expect(showing).toBeVisible({ timeout: 10_000 });
  });

  test('pagination: navigate pages', async ({ page }) => {
    const showingText = await page.locator('text=/Showing/').first().textContent() ?? '';
    const match = showingText.match(/(\d+)\s*[-–]\s*(\d+)\s+.*?(\d+)/);
    const to = match ? parseInt(match[2], 10) : 0;
    const total = match ? parseInt(match[3], 10) : 0;

    if (to >= total) {
      test.skip(true, 'All items fit on one page — nothing to paginate');
      return;
    }

    const nextButton = page.locator('text="Next"').first();
    const textBefore = showingText;
    await nextButton.click();
    await page.waitForTimeout(1_000);
    const textAfter = await page.locator('text=/Showing/').first().textContent();
    expect(textAfter).not.toBe(textBefore);
  });

  // The default limit is 25, so each test changes to a different value first:
  // a click that changes nothing fails instead of passing.
  test('pagination: change limit to 50', async ({ page }) => {
    await changeLimit(page, 50);
  });

  test('pagination: change limit to 25', async ({ page }) => {
    await changeLimit(page, 50);
    await changeLimit(page, 25);
  });

  test('pagination: change limit to 100', async ({ page }) => {
    await changeLimit(page, 100);
  });
});

// R46LANE-FU-5: sixteen workers share one org and the create/actions specs add and delete teams meanwhile, so
// these tests search for teams of their own (a prefix unique to this run and worker) and assert on those rows by
// name, after the page's own list request for that search has answered, never on the org's total.
test.describe('Teams Table search', () => {
  const prefix = `E2E TeamSearch ${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const created: string[] = [];

  test.afterEach(async ({ apiContext }) => {
    for (const id of created.splice(0)) {
      const response = await apiContext.delete(`/api/v1/teams/${id}`);
      if (!response.ok() && response.status() !== 404) {
        throw new Error(`cleanup of team ${id} failed [${response.status()}]: ${await response.text()}`);
      }
    }
  });

  test('search filters teams and clearing it restores them', async ({ page, apiContext }, testInfo) => {
    const mine = `${prefix} w${testInfo.workerIndex}`;
    const alpha = `${mine} Alpha`;
    const beta = `${mine} Beta`;
    for (const name of [alpha, beta]) created.push(await createTeam(apiContext, name));

    const initial = listAnswered(page, null);
    await page.goto('/workspace/teams/');
    await initial;
    // The cleared list starts at the newest team, and other workers keep creating teams: the largest page keeps
    // this test's two on it.
    await changeLimit(page, 100);

    const searchInput = page.locator('input[placeholder*="Search"]');
    const rows = getRows(page);
    const alphaRow = rows.filter({ hasText: alpha });
    const betaRow = rows.filter({ hasText: beta });

    const allMine = listAnswered(page, mine);
    await searchInput.fill(mine);
    expect((await allMine).ok()).toBe(true);
    await expect(alphaRow).toHaveCount(1);
    await expect(betaRow).toHaveCount(1);
    await expect(rows.filter({ hasNotText: mine }), 'only this test\'s teams match its prefix').toHaveCount(0);

    const onlyBeta = listAnswered(page, beta);
    await searchInput.fill(beta);
    expect((await onlyBeta).ok()).toBe(true);
    await expect(betaRow).toHaveCount(1);
    await expect(alphaRow).toHaveCount(0);

    const cleared = listAnswered(page, null);
    await searchInput.clear();
    const clearedRequest = new URL((await cleared).url());
    expect(clearedRequest.searchParams.get('page'), 'clearing the search goes back to the first page').toBe('1');
    expect(clearedRequest.searchParams.get('limit'), 'clearing the search keeps the chosen page size').toBe('100');
    await expect(alphaRow).toHaveCount(1);
    await expect(betaRow).toHaveCount(1);
  });

  test('search with no match shows empty state', async ({ page }, testInfo) => {
    const nothing = `${prefix} w${testInfo.workerIndex} no such team`;
    const initial = listAnswered(page, null);
    await page.goto('/workspace/teams/');
    await initial;

    const answered = listAnswered(page, nothing);
    await page.locator('input[placeholder*="Search"]').fill(nothing);
    expect((await answered).ok()).toBe(true);
    await expect(page.getByText('No teams match the applied filters')).toBeVisible();
    await expect(getRows(page)).toHaveCount(0);
  });
});
