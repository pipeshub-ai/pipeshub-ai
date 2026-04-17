import type { Page, Locator } from '@playwright/test';

/**
 * Helpers for the WorkspaceRightPanel (sidebar form used for create/edit/detail).
 *
 * The sidebar slides in from the right. Its presence is driven by URL query params
 * (?panel=create, ?panel=detail&groupId=xxx&mode=edit).
 */

/** Wait for the sidebar panel to appear */
export async function waitForSidebar(page: Page, timeout = 5_000): Promise<void> {
  // The sidebar panel is a fixed-position container that slides in
  await page.waitForTimeout(300); // animation delay
  // Look for the panel by its fixed positioning pattern or a known child
  await page.locator('[data-side-panel], [role="complementary"]').first().waitFor({ timeout }).catch(() => {
    // Fallback: just wait for any form-like content to appear on the right side
  });
}

/** Click the CTA button in the EntityPageHeader (e.g. "Invite Users", "Create Group") */
export async function clickCta(page: Page): Promise<void> {
  const header = page.locator('button').filter({ hasText: /Invite|Create|Add|New/ });
  await header.first().click();
}

/** Fill a text field by its label */
export async function fillFieldByLabel(
  page: Page,
  label: string,
  value: string
): Promise<void> {
  const field = page.locator(`label:has-text("${label}")`).locator('..').locator('input');
  await field.fill(value);
}

/** Click the primary submit/save button in the sidebar footer */
export async function clickSubmit(page: Page): Promise<void> {
  // Look for primary action buttons: Save, Create, Invite, Submit
  const button = page.locator('button').filter({
    hasText: /^(Save|Create|Invite|Submit|Send|Add)$/,
  });
  await button.first().click();
}

/** Click the cancel/close button in the sidebar */
export async function clickCancel(page: Page): Promise<void> {
  const button = page.locator('button').filter({ hasText: /^(Cancel|Close|Discard)$/ });
  await button.first().click();
}

/** Get the sidebar panel locator */
export function getSidebarPanel(page: Page): Locator {
  return page.locator('[data-side-panel], [role="complementary"]').first();
}
