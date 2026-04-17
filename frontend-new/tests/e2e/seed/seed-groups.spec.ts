import { test, expect } from '../fixtures/api-context.fixture';
import { postWithRetry } from '../helpers/api-retry.helper';

const TOTAL_GROUPS = 30;
const UI_GROUPS = 3;
const API_GROUPS = TOTAL_GROUPS - UI_GROUPS;

function groupName(index: number): string {
  return `E2E Group ${String(index).padStart(3, '0')}`;
}

test.describe.serial('Seed Groups', () => {
  test('create 3 groups via UI', async ({ page }) => {
    await page.goto('/workspace/groups/');
    await page.waitForTimeout(2_000);

    for (let i = 1; i <= UI_GROUPS; i++) {
      // Ensure no dialog is open before clicking CTA
      await page.getByRole('dialog').waitFor({ state: 'detached', timeout: 10_000 }).catch(() => {});

      // Click the "Create Group" CTA
      const ctaButton = page.locator('button').filter({ hasText: /Create/ });
      await ctaButton.first().click();

      // Wait for dialog to appear
      const dialog = page.getByRole('dialog');
      await dialog.waitFor({ state: 'visible', timeout: 5_000 });

      // Fill group name
      const nameInput = dialog.locator('input[placeholder="e.g. Data Engineering"]');
      await nameInput.fill(groupName(i));

      // Click "Create Group" inside the dialog
      await dialog.getByRole('button', { name: 'Create Group' }).click();

      // Wait for dialog to close before next iteration
      await dialog.waitFor({ state: 'detached', timeout: 10_000 });
    }
  });

  test('create 27 groups via API', async ({ apiContext }) => {
    test.setTimeout(5 * 60_000);

    for (let j = 0; j < API_GROUPS; j++) {
      const index = UI_GROUPS + j + 1;
      const response = await postWithRetry(apiContext, '/api/v1/userGroups', { name: groupName(index), type: 'custom' });
      if (!response.ok()) {
        const body = await response.text();
        throw new Error(`POST /api/v1/userGroups failed [${response.status()}] for "${groupName(index)}": ${body}`);
      }
    }
  });

  test('verify group count via API', async ({ apiContext }) => {
    const response = await apiContext.get('/api/v1/userGroups', {
      params: { limit: 1, page: 1 },
    });
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const total = data.pagination?.totalCount ?? data.groups?.length ?? 0;
    expect(total).toBeGreaterThanOrEqual(TOTAL_GROUPS);
  });
});
