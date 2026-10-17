/**
 * Deleting an artifact from the All Artifacts gallery. The list and DELETE
 * endpoints are stubbed with page.route(), so no artifact has to exist.
 */

import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';

const OWNED = {
  artifactId: 'e2e-art-owned',
  name: 'e2e-owned-report.csv',
  artifactType: 'DATA_FILE',
  mimeType: 'text/csv',
  version: 1,
  role: 'OWNER',
};

const SHARED = {
  artifactId: 'e2e-art-shared',
  name: 'e2e-shared-report.csv',
  artifactType: 'DATA_FILE',
  mimeType: 'text/csv',
  version: 1,
  role: 'READER',
};

async function stubArtifacts(page: Page, deleteStatus: number) {
  const items = [OWNED, SHARED];
  const deleted: string[] = [];

  await page.route(/\/api\/v1\/artifacts(\?.*)?$/, (route) => {
    const visible = items.filter((item) => !deleted.includes(item.artifactId));
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: visible,
        pagination: { page: 1, limit: 50, totalCount: visible.length, totalPages: 1 },
      }),
    });
  });

  await page.route(/\/api\/v1\/artifacts\/[^/?]+$/, (route) => {
    if (route.request().method() !== 'DELETE') return route.continue();
    if (deleteStatus === 204) {
      deleted.push(decodeURIComponent(route.request().url().split('/').pop() ?? ''));
      return route.fulfill({ status: 204, body: '' });
    }
    return route.fulfill({
      status: deleteStatus,
      contentType: 'application/json',
      body: JSON.stringify({ error: { message: 'Only the owner can delete this artifact' } }),
    });
  });
}

function row(page: Page, name: string) {
  return page.getByRole('button', { name, exact: true });
}

async function openRowMenu(page: Page, name: string) {
  await page.getByRole('button', { name: `More options for ${name}` }).click();
  return page.getByRole('menu');
}

async function openDeleteDialog(page: Page) {
  await page.goto('/artifacts/');
  await expect(row(page, OWNED.name)).toBeVisible({ timeout: 30_000 });
  await (await openRowMenu(page, OWNED.name)).getByRole('menuitem', { name: /Delete$/ }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText(OWNED.name);
  return dialog;
}

test.describe('Deleting an artifact from the gallery', () => {
  test('cancel keeps the artifact', async ({ page }) => {
    await stubArtifacts(page, 204);
    const dialog = await openDeleteDialog(page);

    await dialog.getByRole('button', { name: 'Cancel' }).click();

    await expect(dialog).toBeHidden();
    await expect(row(page, OWNED.name)).toBeVisible();
  });

  test('confirm removes the artifact and confirms with a toast', async ({ page }) => {
    await stubArtifacts(page, 204);
    const dialog = await openDeleteDialog(page);

    await dialog.getByRole('button', { name: 'Delete' }).click();

    await expect(row(page, OWNED.name)).toBeHidden();
    await expect(page.getByText(`"${OWNED.name}" deleted`)).toBeVisible();
  });

  test('a refused delete keeps the artifact', async ({ page }) => {
    await stubArtifacts(page, 403);
    const dialog = await openDeleteDialog(page);

    await dialog.getByRole('button', { name: 'Delete' }).click();

    await expect(dialog).toBeHidden();
    await expect(row(page, OWNED.name)).toBeVisible();
  });

  test('an artifact shared with the viewer has no delete action', async ({ page }) => {
    await stubArtifacts(page, 204);
    await page.goto('/artifacts/');

    await expect(row(page, SHARED.name)).toBeVisible({ timeout: 30_000 });
    const menu = await openRowMenu(page, SHARED.name);
    await expect(menu.getByRole('menuitem', { name: /Download$/ })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: /Delete$/ })).toHaveCount(0);
  });
});
