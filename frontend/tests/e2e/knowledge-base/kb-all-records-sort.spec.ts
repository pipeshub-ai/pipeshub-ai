import * as fs from 'fs';
import * as path from 'path';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { makeKbName, createTestKb, deleteTestKb, uploadFileByApi } from './kb-upload.helpers';

// Written by the setup project once it signs in; without it there is no stack to run against.
const AUTH_FILE = path.resolve(__dirname, '../../../.auth/user.json');

/** Names of the listed rows that belong to this run, in screen order. */
async function listedNames(page: Page, stamp: string): Promise<string[]> {
  const names = await page
    .getByRole('row')
    .evaluateAll((rows) => rows.map((row) => row.getAttribute('aria-label') ?? ''));
  return names.filter((name) => name.includes(stamp));
}

function waitForNameSort(page: Page, order: 'asc' | 'desc') {
  return page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.includes('/knowledge-hub/nodes') &&
      url.searchParams.get('sortBy') === 'name' &&
      url.searchParams.get('sortOrder') === order
    );
  });
}

test.describe('All Records sort by file name', () => {
  test.describe.configure({ mode: 'serial' });
  test.skip(!fs.existsSync(AUTH_FILE), 'no signed-in e2e user (.auth/user.json); start a stack and run the setup project');

  const stamp = String(Date.now());
  // Mixed case on purpose: the order must not put every capital first.
  const files = [`beta ${stamp}.txt`, `Alpha ${stamp}.txt`, `gamma ${stamp}.txt`, `Delta ${stamp}.txt`];
  let kb: { id: string; name: string } | undefined;

  test.beforeAll(async ({ apiContext }) => {
    const probe = await apiContext.get('/api/v1/knowledgeBase?limit=1').catch(() => null);
    test.skip(!probe?.ok(), 'the PipesHub API is not reachable');
    kb = await createTestKb(apiContext, makeKbName('sort'));
    for (const name of files) {
      await uploadFileByApi(kb.id, { name, mimeType: 'text/plain', buffer: Buffer.from(`sort fixture ${name}`) });
    }
  });

  test.afterAll(async ({ apiContext }) => {
    if (kb) await deleteTestKb(apiContext, kb.id);
  });

  test('clicking File name sorts the rows by name, ascending then descending, ignoring case', async ({ page }) => {
    await page.goto(`/knowledge-base?view=all-records&nodeType=app&nodeId=${kb!.id}`);
    await expect
      .poll(() => listedNames(page, stamp), { message: 'every uploaded file should be listed', timeout: 30_000 })
      .toHaveLength(files.length);

    const header = page.getByRole('button', { name: 'File name', exact: true });
    // The listed name may drop the extension, so rows are compared by their first word.
    const firstWords = async () => (await listedNames(page, stamp)).map((n) => n.split(' ')[0].toLowerCase());

    const ascending = waitForNameSort(page, 'asc');
    await header.click();
    expect((await ascending).status()).toBe(200);
    await expect(page).toHaveURL(/sortField=name/);
    await expect.poll(firstWords).toEqual(['alpha', 'beta', 'delta', 'gamma']);
    await expect(page.getByRole('columnheader', { name: 'File name', exact: true })).toHaveAttribute('aria-sort', 'ascending');

    const descending = waitForNameSort(page, 'desc');
    await header.click();
    expect((await descending).status()).toBe(200);
    await expect.poll(firstWords).toEqual(['gamma', 'delta', 'beta', 'alpha']);
    await expect(page.getByRole('columnheader', { name: 'File name', exact: true })).toHaveAttribute('aria-sort', 'descending');
  });
});
