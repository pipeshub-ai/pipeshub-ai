import { test, expect } from '../fixtures/api-context.fixture';
import { makeKbName, createTestKb, deleteTestKb } from './kb-upload.helpers';

test.describe('Delete a collection from the Collections list', () => {
  let kb: { id: string; name: string };

  test.beforeAll(async ({ apiContext }) => {
    kb = await createTestKb(apiContext, makeKbName('delete-from-row'));
  });

  // Removes the collection if the delete under test did not.
  test.afterAll(async ({ apiContext }) => {
    await deleteTestKb(apiContext, kb.id);
  });

  test('the row menu deletes the collection', async ({ page }) => {
    await page.goto('/knowledge-base/');
    const row = page.getByRole('row').filter({ hasText: kb.name }).first();
    await expect(row).toBeVisible({ timeout: 30_000 });
    await row.hover();
    await row.getByRole('button', { name: 'more_horiz' }).click();
    await page.getByRole('menuitem', { name: /Delete/ }).click();

    const dialog = page.getByRole('dialog');
    const deleted = page.waitForResponse(
      (r) => r.request().method() === 'DELETE' && r.url().includes(kb.id),
      { timeout: 30_000 },
    );
    await dialog.getByRole('textbox').fill('DELETE');
    await dialog.getByRole('button', { name: /Delete/ }).last().click();
    expect((await deleted).ok(), 'the delete request should succeed').toBe(true);
    await expect(dialog).toBeHidden({ timeout: 30_000 });
    await expect(page.getByRole('row').filter({ hasText: kb.name })).toHaveCount(0, { timeout: 30_000 });
  });
});
