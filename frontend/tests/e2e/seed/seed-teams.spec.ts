import { test, expect } from '../fixtures/api-context.fixture';
import { postWithRetry } from '../helpers/api-retry.helper';

const TOTAL_TEAMS = 30;
const UI_TEAMS = 3;
const API_TEAMS = TOTAL_TEAMS - UI_TEAMS;

function teamName(index: number): string {
  return `E2E Team ${String(index).padStart(4, '0')}`;
}

test.describe.serial('Seed Teams', () => {
  test('create 3 teams via UI', async ({ page }) => {
    await page.goto('/workspace/teams/');
    await page.waitForTimeout(2_000);

    for (let i = 1; i <= UI_TEAMS; i++) {
      // Ensure no dialog is open before clicking CTA
      if (await page.getByRole('dialog').isVisible()) {
        await page.getByRole('dialog').waitFor({ state: 'detached', timeout: 10_000 });
      }

      // Click the "Create Team" CTA
      const ctaButton = page.locator('button').filter({ hasText: /Create/ });
      await ctaButton.first().click();

      // Wait for dialog to appear
      const dialog = page.getByRole('dialog');
      await dialog.waitFor({ state: 'visible', timeout: 5_000 });

      // Fill team name
      const nameInput = dialog.locator('input[placeholder="e.g. Product Engineering"]');
      await nameInput.fill(teamName(i));

      // Optionally fill description
      const textarea = dialog.locator('textarea[placeholder="Describe the purpose of this team"]');
      if ((await textarea.count()) > 0) {
        await textarea.first().fill(`E2E test team #${i}`);
      }

      // Click "Create Team" inside the dialog
      await dialog.getByRole('button', { name: 'Create Team' }).click();

      // Wait for dialog to close before next iteration
      await dialog.waitFor({ state: 'detached', timeout: 10_000 });
    }
  });

  test('create 27 teams via API', async ({ apiContext }) => {
    test.setTimeout(5 * 60_000);

    for (let j = 0; j < API_TEAMS; j++) {
      const index = UI_TEAMS + j + 1;
      const response = await postWithRetry(apiContext, '/api/v1/teams', { name: teamName(index) });
      if (!response.ok()) {
        const body = await response.text();
        throw new Error(`POST /api/v1/teams failed [${response.status()}] for "${teamName(index)}": ${body}`);
      }
    }
  });

  test('verify team count via API', async ({ apiContext }) => {
    const response = await apiContext.get('/api/v1/teams/user/teams', {
      params: { limit: 1, page: 1 },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const total = data.pagination?.total ?? data.teams?.length ?? 0;
    expect(total).toBeGreaterThanOrEqual(TOTAL_TEAMS);
  });
});
