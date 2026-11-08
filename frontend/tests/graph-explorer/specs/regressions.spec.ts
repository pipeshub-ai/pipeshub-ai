// Each test here was a finding from a review round: it fails if the fix is removed.
import { expect, test } from '@playwright/test';
import { cyNodeIds, cyNodePoint, cySelected, direction, fake, idle, openExplorer, urlParams, waitForNodes, FOCUS_BADGE } from './helpers';

test.describe('regressions found in review', () => {
  test('a search from the empty start page draws the picked graph, with no workaround', async ({ page }) => {
    await openExplorer(page, '/?repo=acme/shop');
    const box = page.getByRole('combobox', { name: 'Search code' });
    await box.fill('fn_12');
    await expect(page.getByRole('option').first()).toBeVisible();
    await box.press('Enter');
    await waitForNodes(page, 2);
    await expect(page.locator('canvas').first()).toBeVisible();
  });

  test('clicks land on the node when a banner appears while the pointer stays inside the picture', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 3);
    const ids = await cyNodeIds(page);
    const [first, target] = ids.filter((id) => id !== 'block:b0001');
    // Double-click the centre on the picture: nothing new comes back, "Nothing more to show" appears over the
    // picture's top edge and does not move it, and the pointer never leaves it.
    const centre = await cyNodePoint(page, 'block:b0001');
    await page.mouse.move(centre!.x, centre!.y);
    const top = (await page.locator('canvas').first().boundingBox())!.y;
    await page.mouse.dblclick(centre!.x, centre!.y);
    await expect(page.getByText('Nothing more to show for this node.')).toBeVisible();
    expect((await page.locator('canvas').first().boundingBox())!.y).toBe(top);
    const point = await cyNodePoint(page, target);
    await page.mouse.click(point!.x, point!.y);
    await expect.poll(() => cySelected(page)).toEqual([target]);
    void first;
  });

  test('review finding (any move of the picture without a resize): the click still lands', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 3);
    const target = (await cyNodeIds(page)).find((id) => id !== 'block:b0001')!;
    const first = await cyNodePoint(page, target);
    await page.mouse.move(first!.x + 40, first!.y + 40);
    // Slide the picture's box down 56 px with a transform: the size does not change, so nothing resizes.
    await page.evaluate(() => {
      const canvas = document.querySelector('canvas')!.closest('[aria-busy]') as HTMLElement;
      canvas.style.transform = 'translateY(56px)';
    });
    const moved = await cyNodePoint(page, target);
    expect(moved!.y - first!.y).toBeGreaterThan(40);
    await page.mouse.move(moved!.x + 30, moved!.y + 30);
    await page.mouse.click(moved!.x, moved!.y);
    await expect.poll(() => cySelected(page)).toEqual([target]);
  });

  test('after Open the new view is fitted, not left at the old zoom', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0000');
    await waitForNodes(page, 51);
    const zoom = () => page.evaluate(`(() => { const d=[...document.querySelectorAll('div')].find(x=>x._cyreg); return d._cyreg.cy.zoom(); })()`) as Promise<number>;
    const hubZoom = await zoom();
    // Pick a neighbour that is not itself a hub (b0000 to b0011 have dozens) and Open it from the panel: a small view that must fill the picture.
    const target = (await cyNodeIds(page)).find((id) => Number(id.slice(7)) >= 12)!;
    const point = await cyNodePoint(page, target);
    await page.mouse.click(point!.x, point!.y);
    await expect.poll(() => cySelected(page)).toEqual([target]);
    await page.getByRole('button', { name: 'Open', exact: true }).click();
    await expect.poll(() => urlParams(page).get('node')).toBe(target);
    await idle(page);
    await expect.poll(zoom).toBeGreaterThan(hubZoom);
  });

  test('a repository with no graph shows its message on a clear page, not on top of a picture', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    await fake(page, (f) => (f.status = 'never_built'));
    await page.getByRole('button', { name: 'Show as table' }).click();
    await page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1).getByRole('button', { name: 'Open' }).click();
    const message = page.getByText('This repository has no code graph yet.');
    await expect(message).toBeVisible();
    const bg = await message.evaluate((el) => getComputedStyle(el.parentElement!).backgroundColor);
    expect(bg).not.toBe('rgba(0, 0, 0, 0)');
  });

  test('the search list closes on a click elsewhere', async ({ page }) => {
    await openExplorer(page, '/?repo=acme/shop&record=rec-0');
    await waitForNodes(page, 2);
    await page.getByRole('combobox', { name: 'Search code' }).fill('fn_1');
    await expect(page.getByRole('listbox', { name: 'Search results' })).toBeVisible();
    await page.locator('.graph-explorer').click({ position: { x: 5, y: 5 } });
    await expect(page.getByRole('listbox', { name: 'Search results' })).toHaveCount(0);
  });

  test('a full list of matches says to narrow the search', async ({ page }) => {
    await openExplorer(page, '/?repo=acme/shop&record=rec-0');
    await waitForNodes(page, 2);
    await page.getByRole('combobox', { name: 'Search code' }).fill('fn_');
    await expect(page.getByText('Showing the first matches.')).toBeVisible();
  });

  test('the hover card stays inside the picture', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 3);
    const target = (await cyNodeIds(page)).find((id) => id !== 'block:b0001')!;
    const point = await cyNodePoint(page, target);
    await page.mouse.move(point!.x - 3, point!.y - 3);
    await page.mouse.move(point!.x, point!.y);
    const tip = page.getByRole('tooltip');
    await expect(tip).toBeVisible();
    const box = await tip.boundingBox();
    const area = await page.locator('canvas').first().boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(area!.x + area!.width + 1);
    expect(box!.y + box!.height).toBeLessThanOrEqual(area!.y + area!.height + 1);
  });

  test('two quick filter changes leave the controls on the last one and the picture to match', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0000', { latencyMs: 250 });
    await waitForNodes(page, 51);
    await direction(page, 'inbound').click();
    await direction(page, 'outbound').click();
    await idle(page);
    await expect(direction(page, 'outbound')).toHaveAttribute('aria-checked', 'true');
    expect(urlParams(page).get('dir')).toBe('outbound');
    const wanted = await fake(page, (f) => f.visibleNeighbours('block:b0000', 'outbound', false));
    const drawn = (await cyNodeIds(page)).filter((id) => id !== 'block:b0000');
    expect(drawn.every((id) => wanted.includes(id))).toBe(true);
  });

  test('the page does not take focus when it loads', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    expect(await page.evaluate(() => document.activeElement?.tagName)).toBe('BODY');
  });
});
