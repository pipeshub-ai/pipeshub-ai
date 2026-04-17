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

/** Change the items-per-page limit via the dropdown */
export async function changeLimit(page: Page, limit: 10 | 25 | 50 | 100): Promise<void> {
  // Click the limit dropdown trigger (shows current limit number)
  const limitTrigger = page.locator('text="per page"').locator('..');
  // The trigger is the parent flex containing the current number
  await limitTrigger.locator('..').click();

  // Click the desired value in the dropdown
  await page.locator(`[role="menuitem"]`).filter({ hasText: `${limit} per page` }).click();
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
