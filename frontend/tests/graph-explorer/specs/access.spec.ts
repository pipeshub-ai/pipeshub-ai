import { expect, test } from '@playwright/test';
import { cyNodeIds, fake, fileOfId, openExplorer, tableLabels, urlParams, waitForNodes, FOCUS_BADGE } from './helpers';

/** Files whose functions Bob cannot read: file % 5 == 3. */
const hiddenFromBob = (id: string) => fileOfId(id) % 5 === 3;
const label = (id: string) => `${Number(id.replace('block:b', '')) % 11 === 0 ? 'Class' : 'fn'}_${Number(id.replace('block:b', ''))}`;

/** Open b0001 with the clock under the test's control, and let the first answer in. */
async function openWithClock(page: import('@playwright/test').Page, node = 'block:b0001') {
  await page.clock.install();
  await openExplorer(page, `/?node=${node}`);
  await page.clock.runFor(500);
  await waitForNodes(page, 2);
}

test.describe('access', () => {
  test('a revoked file leaves the screen within one check, with no trace in the table, the URL or the notices', async ({ page }) => {
    await openWithClock(page);
    const before = await cyNodeIds(page);
    const focusFile = fileOfId('block:b0001');
    const victim = before.map(fileOfId).find((f) => f !== focusFile)!;
    const victims = before.filter((id) => fileOfId(id) === victim);
    expect(victims.length).toBeGreaterThan(0);
    await fake(page, (f, file) => f.revoked.add(file), victim);
    await page.clock.runFor(36_000);
    const after = await cyNodeIds(page);
    for (const id of victims) expect(after).not.toContain(id);
    expect(after.length).toBe(before.length - victims.length - (await orphans(page, before, victims)));
    await page.getByRole('button', { name: 'Show as table' }).click();
    const text = await page.locator('body').innerText();
    for (const id of victims) expect(text).not.toMatch(new RegExp(`\\b${label(id)}\\b`));
    expect(urlParams(page).get('node')).toBe('block:b0001');
  });

  test('when the focus itself is revoked the explorer says the view is gone', async ({ page }) => {
    await openWithClock(page);
    await fake(page, (f, file) => f.revoked.add(file), fileOfId('block:b0001'));
    await page.clock.runFor(36_000);
    await expect(page.getByText('The item you were looking at is no longer available.')).toBeVisible();
    expect(await cyNodeIds(page)).toEqual([]);
    expect(urlParams(page).has('node')).toBe(false);
  });

  test('a selected node that is revoked leaves the panel with the planned sentence and no trace of its name', async ({ page }) => {
    await openWithClock(page);
    await page.getByRole('button', { name: 'Show as table' }).click();
    const row = page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1);
    const name = (await row.getByRole('rowheader').innerText()).trim();
    const id = `block:b${String(Number(name.replace(/\D/g, ''))).padStart(4, '0')}`;
    expect(fileOfId(id)).not.toBe(fileOfId('block:b0001'));
    await row.click();
    await expect(page.getByRole('heading', { level: 2, name })).toBeVisible();
    await fake(page, (f, file) => f.revoked.add(file), fileOfId(id));
    await page.clock.runFor(36_000);
    await expect(page.getByText('The item you selected is no longer available.')).toBeVisible();
    await expect(page.locator('body')).not.toContainText(name + ' ');
  });

  test('Bob sees nothing from files he cannot read, in the picture, the table or the panel, and no hint that anything is missing', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0000', { persona: 'bob' });
    await waitForNodes(page, 2);
    const drawn = await cyNodeIds(page);
    expect(drawn.filter(hiddenFromBob)).toEqual([]);
    const expected = await fake(page, (f) => f.visibleNeighbours('block:b0000', 'any', false));
    // First page: every one of them is a node Bob may read.
    expect(drawn.slice(1).every((id) => expected.includes(id))).toBe(true);
    await page.getByRole('button', { name: 'Show as table' }).click();
    expect((await tableLabels(page)).length).toBe(drawn.length);
    const text = await page.locator('body').innerText();
    expect(text).not.toMatch(/hidden|restricted|private|no access|more items|missing/i);
  });

  test('Bob has the same words for a node he cannot read as for one that does not exist', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0075', { persona: 'bob' });
    await expect(page.getByText("This item isn't available.")).toBeVisible();
    const hidden = await page.locator('.graph-explorer').innerText();
    await openExplorer(page, '/?node=block:b9999', { persona: 'bob' });
    await expect(page.getByText("This item isn't available.")).toBeVisible();
    expect(await page.locator('.graph-explorer').innerText()).toBe(hidden);
  });

  test('Bob fills his page with nodes he may read, so the page size says nothing about what he may not', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001', { persona: 'bob' });
    await waitForNodes(page, 2);
    const drawn = (await cyNodeIds(page)).filter((id) => id !== 'block:b0001');
    const readable = await fake(page, (f) => f.visibleNeighbours('block:b0001', 'any', false));
    expect(drawn.every((id) => readable.includes(id))).toBe(true);
    expect(drawn.length).toBe(Math.min(50, readable.length));
  });
});

/** Nodes left with no link once the victims went: the explorer drops them too, so they are not on screen. */
async function orphans(page: import('@playwright/test').Page, before: string[], victims: string[]): Promise<number> {
  const after = await cyNodeIds(page);
  return before.filter((id) => !victims.includes(id) && !after.includes(id)).length;
}
