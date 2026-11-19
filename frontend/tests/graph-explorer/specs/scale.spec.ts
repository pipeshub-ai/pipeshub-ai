import { expect, test } from '@playwright/test';
import { cyNodeIds, openExplorer, waitForNodes } from './helpers';

test.describe('node caps', () => {
  test('past 300 nodes the view says it is crowded; past 1,000 the picture stops at 1,000 while the table lists everything in pages of 200', async ({ page }) => {
    test.setTimeout(120_000);
    await openExplorer(page, '/?node=block:b0000', { dense: true });
    await waitForNodes(page, 51);
    await page.getByRole('button', { name: 'Show as table' }).click();
    // Each Expand adds 50 new neighbours; the first rows of the table are nodes that can be expanded.
    for (let i = 1; i <= 24; i++) {
      await page.getByRole('row').nth(i).getByRole('button', { name: 'Expand' }).click();
      await page.waitForTimeout(60);
    }
    await expect(page.getByText(/getting crowded|shows only part of this view/)).toBeVisible();
    const firstPage = await page.getByRole('row').count();
    expect(firstPage).toBe(201); // 200 rows and the header
    await page.getByRole('button', { name: 'Show more rows' }).click();
    expect(await page.getByRole('row').count()).toBe(401);
    await page.getByRole('button', { name: 'Show as graph' }).click();
    await expect.poll(async () => (await cyNodeIds(page)).length).toBeGreaterThan(900);
    expect((await cyNodeIds(page)).length).toBeLessThanOrEqual(1000);
    await expect(page.getByText('The picture shows only part of this view.')).toBeVisible();
  });

  test('the view ceiling: past 5,000 nodes it says the view is full instead of asking again', async ({ page }) => {
    test.setTimeout(240_000);
    await openExplorer(page, '/?node=block:b0000', { dense: true });
    await waitForNodes(page, 51);
    await page.getByRole('button', { name: 'Show as table' }).click();
    for (let i = 1; i <= 120 && !(await page.getByText('This view is full.').count()); i++) {
      await page.getByRole('row').nth(((i - 1) % 190) + 1).getByRole('button', { name: 'Expand' }).click();
      await page.waitForTimeout(40);
    }
    await expect(page.getByText('This view is full. Open a node to start a new one.')).toBeVisible();
  });
});
