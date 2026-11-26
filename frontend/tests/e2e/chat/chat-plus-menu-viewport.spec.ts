/**
 * WP-71: the "+" menu with the agent harness's rows (repository, run gates, the harness switch) is taller than the
 * room above the composer on a 1280×720 window. It must stay inside the window at every size, so "Attach files", its
 * first row, can be clicked. At phone width (390 px) the menu is a bottom sheet and must fit there too.
 */
import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function harnessOn(page: Page): Promise<void> {
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    route.fulfill(json({ featureFlags: {}, agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled' } })),
  );
  await page.route('**/api/v1/conversations*', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill(
          json({
            conversations: [],
            source: 'owned',
            pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
          }),
        )
      : route.continue(),
  );
}

/** Where a box sits against the window: polled, since the phone sheet slides up into place. */
async function expectInsideViewport(page: Page, target: Locator): Promise<void> {
  const size = page.viewportSize();
  expect(size).not.toBeNull();
  await expect
    .poll(async () => {
      const box = await target.boundingBox();
      if (!box || !size) return 'not laid out';
      const out = [
        box.y < 0 && `top ${box.y}`,
        box.x < 0 && `left ${box.x}`,
        box.y + box.height > size.height + 1 && `bottom ${box.y + box.height} > ${size.height}`,
        box.x + box.width > size.width + 1 && `right ${box.x + box.width} > ${size.width}`,
      ].filter(Boolean);
      return out.length ? out.join(', ') : 'inside';
    }, { timeout: 5_000 })
    .toBe('inside');
}

for (const viewport of [
  { width: 1280, height: 720 },
  { width: 1280, height: 560 },
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`WP-71 PM-1: at ${viewport.width}×${viewport.height} the "+" menu with the harness rows stays in the window and Attach files opens the upload`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await harnessOn(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });

    await page.getByRole('button', { name: /^Attach files and capabilities/ }).filter({ visible: true }).first().click();
    const menu = viewport.width < 768 ? page.getByRole('dialog') : page.locator('[data-composer-popover]').filter({ visible: true });
    await expect(menu).toBeVisible({ timeout: 5_000 });
    // The harness rows are in it: the case that ran off the top.
    await expect(menu.getByText('New agent harness')).toBeAttached();
    await expectInsideViewport(page, menu);

    const attach = menu.getByText('Attach files', { exact: true });
    await expectInsideViewport(page, attach);
    await attach.click();
    await expect(menu).toBeHidden({ timeout: 5_000 });
    await expect(page.locator('input[type="file"]').first()).toBeAttached({ timeout: 5_000 });
  });
}
