import { expect, test } from '@playwright/test';
import { cyEdgeCount, cyNodeIds, cyNodePoint, cySelected, direction, fake, fileOfId, idle, openExplorer, tableLabels, urlParams, waitForNodes, FOCUS_BADGE } from './helpers';

const HUB = 'block:b0000';

test.describe('paging, table and trail', () => {
  test('Show more reaches every caller and callee exactly once, then the button goes away', async ({ page }) => {
    await openExplorer(page, `/?node=${HUB}`);
    await waitForNodes(page, 51);
    const more = page.getByRole('button', { name: 'Show more' });
    for (let i = 0; i < 20 && (await more.count()); i++) {
      await more.click();
      await page.waitForTimeout(80);
    }
    await expect(more).toHaveCount(0);
    const drawn = await cyNodeIds(page);
    const wanted = await fake(page, (f) => f.visibleNeighbours('block:b0000', 'any', false));
    expect(new Set(drawn).size).toBe(drawn.length);
    expect(drawn.sort()).toEqual([HUB, ...wanted].sort());
  });

  test('the table lists the same nodes as the picture, the centre first, with dashes for nodes never expanded', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    const drawn = (await cyNodeIds(page)).length;
    await page.getByRole('button', { name: 'Show as table' }).click();
    const labels = await tableLabels(page);
    expect(labels.length).toBe(drawn);
    expect(labels[0]).toBe('fn_1');
    await expect(page.getByRole('row').nth(1)).toContainText(FOCUS_BADGE);
    const dashes = page.getByRole('cell', { name: '–' });
    expect(await dashes.count()).toBeGreaterThan(0);
    await page.getByRole('button', { name: 'Show as graph' }).click();
    await waitForNodes(page, drawn);
  });

  test('Expand adds neighbours in place; expanding a node that has nothing new says so', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    const before = await cyNodeIds(page);
    await page.getByRole('button', { name: 'Show as table' }).click();
    await page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1).getByRole('button', { name: 'Expand' }).click();
    await page.getByRole('button', { name: 'Show as graph' }).click();
    await expect.poll(async () => (await cyNodeIds(page)).length).toBeGreaterThan(before.length);
    const after = await cyNodeIds(page);
    expect(before.filter((id) => !after.includes(id))).toEqual([]); // every earlier node is still drawn
    await page.getByRole('button', { name: 'Show as table' }).click();
    await page.getByRole('row').nth(1).getByRole('button', { name: 'Expand' }).click(); // the centre: already expanded
    await expect(page.getByText('Nothing more to show for this node.')).toBeVisible();
  });

  test('Open makes a new view; Back and Forward walk the trail and the URL follows', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    await page.getByRole('button', { name: 'Show as table' }).click();
    const row = page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1);
    await row.getByRole('button', { name: 'Open' }).click();
    await expect.poll(() => urlParams(page).get('node')).not.toBe('block:b0001');
    const second = urlParams(page).get('node');
    await page.getByRole('button', { name: 'Back' }).click();
    await expect.poll(() => urlParams(page).get('node')).toBe('block:b0001');
    await page.getByRole('button', { name: 'Forward' }).click();
    await expect.poll(() => urlParams(page).get('node')).toBe(second);
  });

  test("the browser's Back and Forward follow the centre, and a filter change is not a step", async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    await page.getByRole('button', { name: 'Show as table' }).click();
    await page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1).getByRole('button', { name: 'Open' }).click();
    await expect.poll(() => urlParams(page).get('node')).not.toBe('block:b0001');
    const second = urlParams(page).get('node');
    await page.getByRole('radio', { name: 'Callers' }).click();
    await expect.poll(() => urlParams(page).get('dir')).not.toBeNull();
    await page.goBack(); // undoes the Open, not the filter
    await expect.poll(() => urlParams(page).get('node')).toBe('block:b0001');
    await expect(page.getByRole('row').filter({ hasText: FOCUS_BADGE })).toContainText('fn_1');
    await expect(page.getByRole('button', { name: 'Forward' })).toBeEnabled(); // the trail kept the step it went Back from
    await page.goForward();
    await expect.poll(() => urlParams(page).get('node')).toBe(second);
  });

  test('Back skips a trail entry that is no longer available and lands on one that is', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    await page.getByRole('button', { name: 'Show as table' }).click();
    const trail = ['block:b0001'];
    for (let step = 0; step < 2; step++) {
      const before = urlParams(page).get('node');
      const used = trail.map(fileOfId);
      // Open a node from a file no earlier node is in, so revoking one file takes away exactly one trail entry.
      const index = await page.getByRole('row').evaluateAll(
        (rows, [usedFiles, badge]) =>
          rows.findIndex((row, i) => {
            const name = row.querySelector('th')?.textContent ?? '';
            const n = Number(name.replace(/\D/g, ''));
            return i > 0 && !name.includes(badge) && name.length > 0 && !usedFiles.includes(Math.floor(n / 25));
          }),
        [used, FOCUS_BADGE] as const,
      );
      expect(index).toBeGreaterThan(0);
      await page.getByRole('row').nth(index).getByRole('button', { name: 'Open' }).click();
      await expect.poll(() => urlParams(page).get('node')).not.toBe(before);
      trail.push(urlParams(page).get('node')!);
      await idle(page);
    }
    const [first, middle] = trail;
    expect(new Set(trail.map(fileOfId)).size).toBe(3);
    await fake(page, (f, file) => f.revoked.add(file), fileOfId(middle));
    const entries = await page.evaluate(() => history.length);
    await page.getByRole('button', { name: 'Back' }).click();
    await expect.poll(() => urlParams(page).get('node')).toBe(first);
    await expect(page.getByRole('alert')).toHaveCount(0);
    await idle(page);
    expect((await tableLabels(page)).length).toBeGreaterThan(1);
    // The skip is one step in the browser's history, and the browser's Back returns to where it left from.
    expect(await page.evaluate(() => history.length)).toBe(entries + 1);
    await page.goBack();
    await expect.poll(() => urlParams(page).get('node')).toBe(trail[2]);
    await idle(page);
    await expect(page.getByRole('row').filter({ hasText: FOCUS_BADGE })).toBeVisible();
    // The entry the browser landed on is the visit: its address is corrected in place, with no new entry on top of it.
    await expect.poll(() => urlParams(page).get('at')).toBe('1');
    expect(await page.evaluate(() => history.length)).toBe(entries + 1);
    // Nothing was pushed over the entry we came back to, so Forward still reaches the node the skip landed on.
    await page.goForward();
    await expect.poll(() => urlParams(page).get('node')).toBe(first);
    await page.goBack();
    await expect.poll(() => urlParams(page).get('node')).toBe(trail[2]);
    await idle(page);
    // Back past the skip: the skipped node is gone, so the centre stays, and still nothing is pushed.
    await page.goBack();
    await idle(page);
    await expect.poll(() => urlParams(page).get('node')).toBe(trail[2]);
    expect(await page.evaluate(() => history.length)).toBe(entries + 1);
  });

  test('the Callers filter shows only callers and re-opens the view; a failed re-open puts the filter back', async ({ page }) => {
    await openExplorer(page, `/?node=${HUB}`);
    await waitForNodes(page, 51);
    await direction(page, 'inbound').click();
    await expect.poll(() => urlParams(page).get('dir')).toBe('inbound');
    await idle(page);
    await expect.poll(async () => (await cyEdgeCount(page)) > 0).toBe(true);
    await fake(page, (f) => (f.outage = true));
    await direction(page, 'outbound').click();
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
    await expect(direction(page, 'inbound')).toHaveAttribute('aria-checked', 'true');
    expect(urlParams(page).get('dir')).toBe('inbound');
  });

  test('clicking a node selects it and the panel shows it; Escape clears it', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    const ids = await cyNodeIds(page);
    const target = ids.find((id) => id !== 'block:b0001')!;
    const point = await cyNodePoint(page, target);
    await page.mouse.click(point!.x, point!.y);
    await expect.poll(() => cySelected(page)).toEqual([target]);
    await page.keyboard.press('Escape');
    await expect.poll(() => cySelected(page)).toEqual([]);
  });
});
