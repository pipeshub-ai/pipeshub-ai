import type { Page } from '@playwright/test';

/**
 * Helpers for interacting with the EntityPageHeader search field.
 *
 * The search field is a Radix TextField.Root with a search icon slot,
 * rendered at 224px width in the page header.
 */

/** Get the search input element */
function getSearchInput(page: Page): ReturnType<Page['locator']> {
  return page.locator('input[placeholder*="Search"]');
}

/** Type a search query into the header search field */
export async function search(page: Page, query: string): Promise<void> {
  const input = getSearchInput(page);
  await input.fill(query);
  // Debounce wait — most implementations debounce 300-500ms
  await page.waitForTimeout(500);
}

/** Clear the search field */
export async function clearSearch(page: Page): Promise<void> {
  const input = getSearchInput(page);
  await input.clear();
  await page.waitForTimeout(500);
}

/** Get the current search field value */
export async function getSearchValue(page: Page): Promise<string> {
  const input = getSearchInput(page);
  return (await input.inputValue()) ?? '';
}
