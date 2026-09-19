import { expect, type Page, type Locator } from '@playwright/test';

/**
 * Helpers for the EntityPagination component.
 *
 * The pagination footer contains:
 * - Left: "Showing X-Y of Z" text
 * - Right: Previous button, page number box, Next button, limit dropdown
 */

/** Get the pagination container (the bottom bar) */
function getPagination(page: Page): Locator {
  return page.locator('text=/Showing \\d+/').locator('..');
}

/** Extract the "Showing X-Y of Z" values */
export async function getShowingText(page: Page): Promise<string> {
  const el = page.locator('text=/Showing/').first();
  return (await el.textContent()) ?? '';
}

/** Parse "Showing X-Y of Z" into { from, to, total } */
export async function getShowingRange(
  page: Page
): Promise<{ from: number; to: number; total: number }> {
  const text = await getShowingText(page);
  const match = text.match(/(\d+)\s*[-–]\s*(\d+)\s+.*?(\d+)/);
  if (!match) {
    return { from: 0, to: 0, total: 0 };
  }
  return {
    from: parseInt(match[1], 10),
    to: parseInt(match[2], 10),
    total: parseInt(match[3], 10),
  };
}

/** Click the "Next" pagination button */
export async function clickNext(page: Page): Promise<void> {
  await page.locator('text="Next"').click();
}

/** Click the "Previous" pagination button */
export async function clickPrevious(page: Page): Promise<void> {
  await page.locator('text="Previous"').click();
}

/** Get the current page number displayed */
export async function getCurrentPage(page: Page): Promise<number> {
  // The page number is inside a Box between Previous and Next
  const prevContainer = page.locator('text="Previous"').locator('..');
  const nextContainer = page.locator('text="Next"').locator('..');
  // Page number is in a sibling Box element
  const parent = prevContainer.locator('..');
  const pageBox = parent.locator('div').filter({ hasText: /^\d+$/ });
  const text = await pageBox.textContent();
  return parseInt(text ?? '1', 10);
}

/** The collapsed page-size control, showing the current limit. "N per page" exists only in its menu. */
function limitTrigger(page: Page): Locator {
  return page.getByTestId('page-size-trigger');
}

/**
 * Change the items-per-page limit, and assert it took effect: the control shows
 * the new number and the "Showing" line starts at 1 and stops at that limit (or
 * the total). Callers should pick a limit other than the current one, so a click
 * that changed nothing can't pass.
 */
export async function changeLimit(page: Page, limit: 10 | 25 | 50 | 100): Promise<void> {
  const trigger = limitTrigger(page);
  await expect(trigger, 'the page-size control should be shown').toBeVisible({ timeout: 10_000 });
  await trigger.click();
  await page.getByRole('menuitem', { name: `${limit} per page` }).click();

  await expect(trigger, `the page-size control should show ${limit}`).toHaveText(
    // The number is followed directly by the icon's ligature text ("50expand_less").
    new RegExp(`^\\s*${limit}(?!\\d)`),
    { timeout: 10_000 },
  );
  await expect
    .poll(
      async () => {
        const { from, to, total } = await getShowingRange(page);
        return from === 1 && to === Math.min(limit, total) ? 'ok' : `Showing ${from}-${to} of ${total}`;
      },
      { timeout: 10_000, message: `the list should show the first ${limit} items` },
    )
    .toBe('ok');
  expect(await page.locator('[role="row"]').count()).toBeLessThanOrEqual(limit);
}

/** Assert the "Showing X-Y of Z" text matches expected range */
export async function expectShowingRange(
  page: Page,
  from: number,
  to: number,
  total: number
): Promise<void> {
  const range = await getShowingRange(page);
  expect(range.from).toBe(from);
  expect(range.to).toBe(to);
  expect(range.total).toBe(total);
}
