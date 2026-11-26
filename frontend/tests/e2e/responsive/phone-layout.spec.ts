/**
 * QAFINAL-FU-AXE-2: what the UX captures saw on a phone. The Collections filter chips ran off the right edge with no
 * sign that the row scrolls (its scrollbar is hidden), and the retention field's "days" wrapped under its box.
 * Run: npx playwright test --project=responsive tests/e2e/responsive/phone-layout.spec.ts
 */
import type { Page } from '@playwright/test';
import { expect, test } from '../fixtures/base.fixture';

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 });

async function settled(page: Page, path: string) {
  await page.goto(path);
  await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => undefined);
}

test('PHONELAYOUT-1: the Collections filter chips stay on screen and say where more of them are', async ({ page }) => {
  await settled(page, '/knowledge-base/');
  const chips = page.locator('.kb-filter-bar-mobile');
  await expect(chips).toBeVisible();
  const row = await chips.evaluate((el) => ({
    right: el.getBoundingClientRect().right,
    width: window.innerWidth,
    overflows: el.scrollWidth > el.clientWidth + 1,
  }));
  expect(row.right).toBeLessThanOrEqual(row.width);
  expect(row.overflows, 'six chips do not fit 390px, so the row scrolls').toBe(true);

  const fade = () => chips.evaluate((el) => getComputedStyle(el).maskImage || getComputedStyle(el).webkitMaskImage);
  await expect(chips).toHaveAttribute('data-more-end', 'true');
  expect(await fade()).toMatch(/linear-gradient/);

  await chips.evaluate((el) => el.scrollTo({ left: el.scrollWidth }));
  await expect(chips).not.toHaveAttribute('data-more-end', 'true');
  await expect(chips).toHaveAttribute('data-more-start', 'true');
  const last = await chips.evaluate((el) => {
    const box = el.getBoundingClientRect();
    const end = (el.lastElementChild as HTMLElement).getBoundingClientRect();
    return { right: end.right, edge: box.right };
  });
  expect(last.right, 'scrolled to its end, the last chip is whole').toBeLessThanOrEqual(last.edge + 1);
});

test('PHONELAYOUT-2: the retention field keeps "days" on the line of its box', async ({ page }) => {
  await settled(page, '/workspace/ai-access/');
  const section = page.getByTestId('conversation-retention');
  await section.scrollIntoViewIfNeeded();
  const box = await page.locator('#retention-days').boundingBox();
  const unit = await section.getByText('days', { exact: true }).boundingBox();
  expect(box && unit).toBeTruthy();
  const middle = unit!.y + unit!.height / 2;
  expect(middle).toBeGreaterThanOrEqual(box!.y);
  expect(middle).toBeLessThanOrEqual(box!.y + box!.height);
  expect(unit!.x).toBeGreaterThanOrEqual(box!.x + box!.width);
});
