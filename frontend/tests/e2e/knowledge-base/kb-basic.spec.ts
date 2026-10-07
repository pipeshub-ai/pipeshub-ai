import { test, expect } from '../fixtures/api-context.fixture';
import { makeKbName, createTestKb, deleteTestKb } from './kb-upload.helpers';

test.describe('Knowledge Base Basic', () => {
  let kb: { id: string; name: string };

  test.beforeAll(async ({ apiContext }) => {
    kb = await createTestKb(apiContext, makeKbName('basic'));
  });

  test.afterAll(async ({ apiContext }) => {
    await deleteTestKb(apiContext, kb.id);
  });

  test('page loads successfully @smoke', async ({ page }) => {
    await page.goto('/knowledge-base/');
    await expect(page).toHaveURL(/\/knowledge-base\//);
  });

  test('a knowledge base created through the API is listed', async ({ page }) => {
    await page.goto('/knowledge-base/');
    await expect(
      page.getByText(kb.name).first(),
      'the knowledge base created in beforeAll should be listed on the page',
    ).toBeVisible({ timeout: 30_000 });
  });

  test('opening a knowledge base shows its name and the New menu', async ({ page }) => {
    await page.goto(`/knowledge-base/?nodeType=app&nodeId=${kb.id}`);
    await expect(page.getByText(kb.name).first()).toBeVisible({ timeout: 30_000 });
    // The New menu is where uploads start; without it a knowledge base can't be filled.
    await expect(page.getByTestId('new-dropdown-trigger')).toBeVisible({ timeout: 30_000 });
  });
});
