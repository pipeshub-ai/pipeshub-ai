import type { APIRequestContext } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';

/** Delete a team this spec created, found by its exact name. */
async function deleteTeamByName(apiContext: APIRequestContext, name: string): Promise<void> {
  const list = await apiContext.get('/api/v1/teams/user/teams', {
    params: { search: name, page: 1, limit: 10 },
  });
  if (!list.ok()) {
    throw new Error(`listing teams to clean up "${name}" failed [${list.status()}]: ${await list.text()}`);
  }
  const body = await list.json();
  const teams: Array<{ id?: string; _key?: string; name?: string }> = body.teams ?? [];
  const team = teams.find((t) => t.name === name);
  if (!team) return;
  const id = team.id ?? team._key;
  // Without an id the DELETE would hit /undefined, get a 404, and pass as "already gone".
  if (!id) throw new Error(`team "${name}" has no id in the list response: ${JSON.stringify(team)}`);
  const response = await apiContext.delete(`/api/v1/teams/${id}`);
  if (!response.ok() && response.status() !== 404) {
    throw new Error(`deleting team "${name}" failed [${response.status()}]: ${await response.text()}`);
  }
}

test.describe('Teams Create', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/workspace/teams/');
    await page.waitForTimeout(3_000);
  });

  test('opens create sidebar when clicking CTA', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Create/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    // URL should update with panel=create
    expect(page.url()).toContain('panel=create');
  });

  test('create team with name and description', async ({ page, apiContext }) => {
    // Unique per run, so repeated or parallel runs never match each other's team.
    const teamName = `E2E Test Create Team ${Date.now()}`;

    try {
      const ctaButton = page.locator('button').filter({ hasText: /Create/ });
      await ctaButton.first().click();

      const nameInput = page.locator('input[placeholder="e.g. Product Engineering"]');
      await expect(nameInput).toBeVisible({ timeout: 5_000 });
      await nameInput.fill(teamName);

      const textarea = page.locator('textarea[placeholder="Describe the purpose of this team"]');
      await expect(textarea, 'the description box should be shown').toBeVisible({ timeout: 5_000 });
      await textarea.fill('Created by E2E tests');

      // Submit inside the dialog (not the page CTA behind the overlay)
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('button', { name: 'Create Team' }).click();
      await expect(dialog).toBeHidden({ timeout: 10_000 });

      const searchInput = page.locator('input[placeholder*="Search"]');
      await expect(searchInput).toBeVisible({ timeout: 10_000 });
      await searchInput.fill(teamName);
      await expect(
        page.locator('[role="row"]').filter({ hasText: teamName }).first(),
      ).toBeVisible({ timeout: 10_000 });
    } finally {
      await deleteTeamByName(apiContext, teamName);
    }
  });

  test('validation: empty name prevents creation', async ({ page }) => {
    const ctaButton = page.locator('button').filter({ hasText: /Create/ });
    await ctaButton.first().click();
    await page.waitForTimeout(500);

    const dialog = page.getByRole('dialog');
    const submitButton = dialog.getByRole('button', { name: 'Create Team' });
    await expect(submitButton).toBeDisabled();
  });
});
