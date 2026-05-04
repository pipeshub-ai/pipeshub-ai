import type { Page, Locator } from '@playwright/test';

/**
 * Helpers for interacting with the EntityDataTable component.
 *
 * Rows are Flex elements with role="row".
 * Each row has a checkbox as the first child Flex, data cells, and optional action buttons.
 */

/** Get all visible data rows */
export function getRows(page: Page): Locator {
  return page.locator('[role="row"]');
}

/** Get a specific row by its zero-based index */
export function getRow(page: Page, index: number): Locator {
  return getRows(page).nth(index);
}

/** Click the checkbox in a specific row */
export async function toggleRowCheckbox(page: Page, index: number): Promise<void> {
  const row = getRow(page, index);
  await row.locator('button[role="checkbox"]').click();
}

/** Click the "select all" checkbox in the table header */
export async function toggleSelectAll(page: Page): Promise<void> {
  // The header checkbox is the first checkbox on the page (before any row checkboxes)
  const headerCheckbox = page.locator('button[role="checkbox"]').first();
  await headerCheckbox.click();
}

/** Get the count of currently selected rows (via aria-selected) */
export async function getSelectedCount(page: Page): Promise<number> {
  return getRows(page).locator('[aria-selected="true"]').count();
}

/** Click on a row to open its detail view */
export async function clickRow(page: Page, index: number): Promise<void> {
  await getRow(page, index).click();
}

/** Get the number of visible rows */
export async function getRowCount(page: Page): Promise<number> {
  return getRows(page).count();
}

/**
 * Wait for the table to have at least one row loaded.
 * Useful after navigation or filter changes.
 */
export async function waitForTableLoaded(page: Page, timeout = 10_000): Promise<void> {
  await page.locator('[role="row"]').first().waitFor({ timeout });
}
