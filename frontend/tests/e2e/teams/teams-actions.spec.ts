import type { APIRequestContext, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { postWithRetry } from '../helpers/api-retry.helper';
import { getRows, waitForTableLoaded } from '../helpers/entity-table.helper';

// Each test creates the team it acts on, so it no longer depends on another
// spec having created "E2E Test Create Team" first.
const created: string[] = [];

async function createTeam(apiContext: APIRequestContext, label: string): Promise<string> {
  const name = `E2E Team Actions ${label} ${Date.now()}`;
  const response = await postWithRetry(apiContext, '/api/v1/teams', { name });
  if (!response.ok()) {
    throw new Error(`POST /api/v1/teams failed [${response.status()}]: ${await response.text()}`);
  }
  const data = await response.json();
  // The API answers {status, message, data: {id, ...}}, so the id is under `data`.
  const id: string | undefined =
    data.data?.id ?? data.data?._key ?? data.team?.id ?? data.id ?? data.team?._key ?? data._key;
  // Without an id the team could never be cleaned up.
  if (!id) throw new Error(`POST /api/v1/teams returned no id: ${JSON.stringify(data)}`);
  created.push(id);
  return name;
}

async function openTeam(page: Page, name: string): Promise<void> {
  await page.goto('/workspace/teams/');
  await page.locator('input[placeholder*="Search"]').first().fill(name);
  const row = getRows(page).filter({ hasText: name }).first();
  await expect(row, `team "${name}" should be listed`).toBeVisible({ timeout: 15_000 });
  await row.click();
}

test.describe('Teams Actions', () => {
  test.afterAll(async ({ apiContext }) => {
    // 404 is expected for the team the delete test already removed.
    for (const id of created) {
      const response = await apiContext.delete(`/api/v1/teams/${id}`);
      if (!response.ok() && response.status() !== 404) {
        throw new Error(`cleanup of team ${id} failed [${response.status()}]: ${await response.text()}`);
      }
    }
  });

  test('clicking a row opens team detail', async ({ page, apiContext }) => {
    const name = await createTeam(apiContext, 'detail');
    await page.goto('/workspace/teams/');
    await waitForTableLoaded(page, 15_000);
    await openTeam(page, name);

    await expect
      .poll(
        async () =>
          page.url().includes('panel=detail') ||
          page.url().includes('teamId=') ||
          (await page
            .locator('[data-side-panel], [role="complementary"]')
            .first()
            .isVisible()
            .catch(() => false)),
        { timeout: 5_000 },
      )
      .toBe(true);
  });

  test('edit team name', async ({ page, apiContext }) => {
    const name = await createTeam(apiContext, 'edit');
    const renamed = `${name} Updated`;
    await openTeam(page, name);

    await page.getByRole('button', { name: 'Edit Team' }).click();
    // Scoped to the panel, and strict: `openTeam` has just typed this name
    // into the list's search box, so an unscoped `input[value=…]` resolves to
    // that box first. The new name went into search, the name field kept the
    // old one, and Save sent the old name -- a rename that "reported success"
    // without ever being asked to change anything.
    const nameInput = page.getByRole('dialog').locator(`input[value="${name}"]`);
    await expect(nameInput).toBeEditable({ timeout: 5_000 });
    await nameInput.fill(renamed);
    await page.getByRole('button', { name: 'Save Edits' }).click();

    await expect(page.getByText('Team updated!').first()).toBeVisible({ timeout: 10_000 });
    await page.goto('/workspace/teams/');
    const search = page.locator('input[placeholder*="Search"]').first();
    await search.fill(renamed);

    // Searching the old name on failure, because "not listed under the new
    // name" has three very different causes and the assertion alone cannot say
    // which: the save did not change the name, the name was saved but this
    // search missed it, or the team stopped matching search altogether. They
    // live in different code, and a nightly failure that names none of them
    // sends whoever picks it up to read the wrong one.
    const renamedRow = getRows(page).filter({ hasText: renamed }).first();
    try {
      await expect(renamedRow).toBeVisible({ timeout: 15_000 });
    } catch (failure) {
      await search.fill(name);
      // Search matches on a substring (`CONTAINS` on Neo4j, `LIKE %search%`
      // on ArangoDB) and the new name is the old one plus a suffix, so this
      // search returns the team under either name. Both rows are waited for
      // together: treating "no old-name row" as absence would report a broken
      // listing while the renamed team sits on screen. `isVisible` is not
      // enough either -- the list only refetches when the search text
      // changes, so an immediate read sees the previous results.
      const visible = (locator: ReturnType<typeof getRows>) =>
        locator.first().waitFor({ state: 'visible', timeout: 15_000 });
      // `race`, not `any`: both waits share one 15s timeout and reject only
      // when it elapses, so the first row to appear still wins and a run where
      // neither appears still lands on `neither`. `any` would work too, but it
      // is ES2021 and this project's `lib` is es6 -- it resolves today only
      // through a dependency that happens to pull the newer lib in.
      const listedAs = await Promise.race([
        visible(getRows(page).filter({ hasText: name, hasNotText: renamed })).then(() => 'old' as const),
        visible(getRows(page).filter({ hasText: renamed })).then(() => 'new' as const),
      ]).catch(() => 'neither' as const);
      const because = {
        old: 'It is still listed under its old name, so the save did not change the name.',
        new: 'Searching its old name does list it under the new one, so the name was saved and the search for the new name is what missed it.',
        neither: 'It is not listed under either name, so it has stopped matching search.',
      }[listedAs];
      throw new Error(
        `the team is not listed as "${renamed}" after a save that reported success. ` +
          because +
          `\n\nOriginal failure: ${(failure as Error).message}`
      );
    }
  });

  test('delete team', async ({ page, apiContext }) => {
    const name = await createTeam(apiContext, 'delete');
    await openTeam(page, name);

    // Delete is offered only in edit mode.
    await page.getByRole('button', { name: 'Edit Team' }).click();
    await page.getByRole('button', { name: 'Delete Team' }).click();

    await expect(page.getByText('Team deleted').first()).toBeVisible({ timeout: 10_000 });
    await page.goto('/workspace/teams/');
    await page.locator('input[placeholder*="Search"]').first().fill(name);
    await expect(getRows(page).filter({ hasText: name })).toHaveCount(0, { timeout: 15_000 });
  });
});
