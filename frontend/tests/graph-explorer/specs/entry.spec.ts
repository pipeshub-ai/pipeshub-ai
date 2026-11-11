import { expect, test } from '@playwright/test';
import { cyNodeIds, openExplorer, urlParams, waitForNodes } from './helpers';

test.describe('entry', () => {
  test('a record link opens the graph and the address becomes the node id, nothing else', async ({ page }) => {
    const { errors } = await openExplorer(page, '/?record=rec-0');
    await waitForNodes(page, 2);
    await expect.poll(() => urlParams(page).get('node')).toMatch(/^block:b\d{4}$/);
    expect(urlParams(page).has('record')).toBe(false);
    expect([...urlParams(page).keys()].every((k) => ['node', 'at', 'dir', 'types', 'tests'].includes(k))).toBe(true);
    expect(errors).toEqual([]);
  });

  test('repo, file and symbol resolve to that function', async ({ page }) => {
    await openExplorer(page, '/?repo=acme/shop&file=src/mod_5/file_5.py&symbol=function:fn_126');
    await waitForNodes(page, 1);
    await expect.poll(() => urlParams(page).get('node')).toBe('block:b0126');
    await expect(page.getByRole('heading', { name: 'fn_126' })).toBeVisible();
  });

  test('a node link opens that node', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    expect((await cyNodeIds(page)).includes('block:b0001')).toBe(true);
  });

  test('a target in a test file is "not available" while tests are off, with the hint, and opens with tests=1', async ({ page }) => {
    await openExplorer(page, '/?record=rec-4');
    await expect(page.getByText("This item isn't available.")).toBeVisible();
    await expect(page.getByText('Test files are hidden. If the item is in test code, turn on Include tests.')).toBeVisible();
    await openExplorer(page, '/?record=rec-4&tests=1');
    await waitForNodes(page, 1);
  });

  test('a record that does not exist says the same words as one the viewer cannot read', async ({ page }) => {
    await openExplorer(page, '/?record=rec-999');
    const missing = await page.getByText("This item isn't available.").innerText();
    await openExplorer(page, '/?record=rec-4');
    expect(await page.getByText("This item isn't available.").innerText()).toBe(missing);
  });

  test('an ambiguous repository name gets a generic sentence and no API wording', async ({ page }) => {
    await openExplorer(page, '/?repo=shop&file=src/mod_5/file_5.py');
    await expect(page.getByText('More than one repository has that name.')).toBeVisible();
    await expect(page.locator('body')).not.toContainText('Call again');
  });

  test('hostile and malformed parameters change nothing and break nothing', async ({ page }) => {
    const { errors } = await openExplorer(page, '/?node=../../etc&dir=sideways&types=<script>alert(1)</script>&tests=yes&record=../x&repo=' + 'a'.repeat(400));
    await expect(page.getByText(/Search for a function/)).toBeVisible();
    expect(await cyNodeIds(page)).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('search first: choose a repository, type, press Enter, and the graph is drawn', async ({ page }) => {
    await openExplorer(page, '/');
    await expect(page.getByLabel('Search code')).toBeDisabled();
    await page.getByRole('combobox', { name: 'Repository' }).click();
    await page.getByRole('option', { name: 'acme/shop' }).click();
    const box = page.getByRole('combobox', { name: 'Search code' });
    await box.fill('fn_12');
    await expect(page.getByRole('listbox', { name: 'Search results' }).getByRole('option').first()).toBeVisible();
    await box.press('Enter');
    await waitForNodes(page, 2);
    await expect.poll(() => urlParams(page).get('node')).toMatch(/^block:/);
  });

  test('"/" goes to the search box, or to the repository choice before one is chosen', async ({ page }) => {
    await openExplorer(page, '/');
    await page.locator('body').click({ position: { x: 5, y: 5 } });
    await page.keyboard.press('/');
    await expect(page.getByRole('combobox', { name: 'Repository' })).toBeFocused();
    await page.keyboard.press('Escape');
    await page.getByRole('combobox', { name: 'Repository' }).click();
    await page.getByRole('option', { name: 'acme/shop' }).click();
    await page.locator('body').click({ position: { x: 5, y: 5 } });
    await page.keyboard.press('/');
    await expect(page.getByRole('combobox', { name: 'Search code' })).toBeFocused();
  });

  test('?repo= alone scopes the search, and an unknown repo is ignored', async ({ page }) => {
    await openExplorer(page, '/?repo=acme/shop');
    await expect(page.getByLabel('Search code')).toBeEnabled();
    await openExplorer(page, '/?repo=acme/nothing');
    await expect(page.getByLabel('Search code')).toBeDisabled();
    await expect(page.getByLabel('Search code')).toHaveAttribute('placeholder', 'Choose a repository first');
  });

  test('a one-character search says to type more, and search results do not push the graph down', async ({ page }) => {
    await openExplorer(page, '/?repo=acme/shop&record=rec-0');
    await waitForNodes(page, 2);
    const before = await page.getByRole('toolbar').boundingBox();
    const box = page.getByRole('combobox', { name: 'Search code' });
    await box.fill('f');
    await expect(page.getByText('Type at least 2 characters.')).toBeVisible();
    expect((await page.getByRole('toolbar').boundingBox())?.y).toBe(before?.y);
    await box.fill('fn_1');
    await expect(page.getByRole('listbox', { name: 'Search results' })).toBeVisible();
    expect((await page.getByRole('toolbar').boundingBox())?.y).toBe(before?.y);
  });
});
