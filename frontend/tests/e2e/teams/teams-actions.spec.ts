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
  const id: string | undefined = data.team?.id ?? data.id ?? data.team?._key ?? data._key;
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
    const nameInput = page.locator(`input[value="${name}"]`).first();
    await expect(nameInput).toBeEditable({ timeout: 5_000 });
    await nameInput.fill(renamed);
    await page.getByRole('button', { name: 'Save Edits' }).click();

    await expect(page.getByText('Team updated!').first()).toBeVisible({ timeout: 10_000 });
    await page.goto('/workspace/teams/');
    await page.locator('input[placeholder*="Search"]').first().fill(renamed);
    await expect(getRows(page).filter({ hasText: renamed }).first()).toBeVisible({ timeout: 15_000 });
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
