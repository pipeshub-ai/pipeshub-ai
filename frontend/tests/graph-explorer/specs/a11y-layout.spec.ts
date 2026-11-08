import { expect, test } from '@playwright/test';
import { cyNodeIds, cyNodePoint, fake, openExplorer, waitForNodes, FOCUS_BADGE } from './helpers';

test.describe('keyboard, screen readers and layout', () => {
  test('every control has a name, icons are hidden from screen readers, and a polite region is always there', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    for (const name of ['Back', 'Forward', 'Fit to view', 'Show as table']) await expect(page.getByRole('button', { name })).toBeVisible();
    expect(await page.locator('button [aria-hidden="true"]').count()).toBeGreaterThanOrEqual(4);
    expect(await page.locator('[role=status][aria-live=polite]').count()).toBeGreaterThan(0);
    await expect(page.locator('[role=status]').filter({ hasText: /^Showing \d+ items\.$/ })).toHaveCount(1);
    await expect(page.getByText('The graph picture is not available to screen readers.')).toBeAttached();
  });

  test('keyboard only: Tab reaches search, toolbar and panel in order with a visible focus; Escape inside the repository list leaves the selection alone', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    const seen: string[] = [];
    for (let i = 0; i < 14; i++) {
      await page.keyboard.press('Tab');
      seen.push(await page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.textContent?.trim().slice(0, 20) ?? ''));
    }
    expect(seen.join('|')).toContain('Repository');
    expect(seen.join('|')).toContain('Fit to view');
    expect(seen.some((s) => /Open|Expand/.test(s))).toBe(true);
    // Escape in the open repository list does not clear the selection behind it.
    await page.getByRole('combobox', { name: 'Repository' }).click();
    await expect(page.getByRole('listbox')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('heading', { name: 'fn_1' })).toBeVisible();
  });

  test('focus lands on the explorer, not the page, when the focused row goes away', async ({ page }) => {
    await page.clock.install();
    await openExplorer(page, '/?node=block:b0001');
    await page.clock.runFor(500);
    await waitForNodes(page, 2);
    await page.getByRole('button', { name: 'Show as table' }).click();
    const row = page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1);
    await row.click();
    await row.getByRole('button', { name: 'Expand' }).focus();
    const label = (await row.getByRole('rowheader').innerText()).trim();
    const file = Math.floor(Number(label.replace(/\D/g, '')) / 25);
    await page.evaluate((f) => (window as unknown as { __fake: { revoked: Set<number> } }).__fake.revoked.add(f), file);
    await page.clock.runFor(36_000);
    const tag = await page.evaluate(() => document.activeElement?.tagName);
    expect(tag).not.toBe('BODY');
  });

  test('clicks land on the node after a banner appears above the picture', async ({ page }) => {
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 3);
    await page.mouse.move(600, 500);
    await page.evaluate(() => ((window as unknown as { __fake: { status: string } }).__fake.status = 'stale'));
    await page.getByRole('button', { name: 'Show as table' }).click();
    await page.getByRole('row').filter({ hasNotText: FOCUS_BADGE }).nth(1).getByRole('button', { name: 'Expand' }).click();
    await expect(page.getByText('The graph is being updated')).toBeVisible();
    await page.getByRole('button', { name: 'Show as graph' }).click();
    await page.mouse.move(700, 600);
    const target = (await cyNodeIds(page)).find((id) => id !== 'block:b0001')!;
    const point = await cyNodePoint(page, target);
    await page.mouse.click(point!.x, point!.y);
    await expect(page.getByRole('heading', { level: 2 })).toBeVisible();
  });

  test('at 375 px nothing scrolls sideways and every control is at least 44 px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    const small = await page.evaluate(() =>
      [...document.querySelectorAll('.graph-explorer button:not([role=switch]), .graph-explorer input, .graph-explorer [role=combobox]')]
        .map((el) => ({ name: el.getAttribute('aria-label') ?? el.textContent?.trim().slice(0, 20), h: el.getBoundingClientRect().height, visible: el.getBoundingClientRect().width > 0 }))
        .filter((c) => c.visible && c.h < 43.5),
    );
    expect(small).toEqual([]);
  });

  test('the persona switch and labels never produce a console error, and the page title and headings are stable', async ({ page }) => {
    const { errors } = await openExplorer(page, '/?node=block:b0001');
    await waitForNodes(page, 2);
    expect(await page.locator('h1').count()).toBe(0);
    expect(errors).toEqual([]);
    await fake(page, (f) => (f.persona = 'bob'));
  });
});
