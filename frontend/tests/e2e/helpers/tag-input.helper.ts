import type { Page, Locator } from '@playwright/test';

/**
 * Helpers for the TagInput component used in user invite flows.
 *
 * TagInput splits pasted text by commas, spaces, and newlines.
 * Each value becomes a pill tag. The input is a plain <input> inside a styled Box.
 */

/** Get the tag input element (the raw <input> inside the tag container) */
function getTagInput(page: Page): Locator {
  const dialog = page.getByRole('dialog');
  return dialog.getByRole('textbox').first();
}

/** Paste multiple emails (comma-separated) into the TagInput */
export async function pasteEmails(page: Page, emails: string[]): Promise<void> {
  const input = getTagInput(page);
  if ((await input.count()) === 0) {
    // Fallback: find any visible text input that's not the search field
    const fallbackInput = page.locator('input[type="text"]').last();
    await fallbackInput.focus();
    await page.evaluate(
      (text) => navigator.clipboard.writeText(text),
      emails.join(', ')
    );
    await fallbackInput.press('ControlOrMeta+v');
    return;
  }

  await input.focus();

  // Use the clipboard API to trigger the paste handler
  await page.evaluate(
    (text) => navigator.clipboard.writeText(text),
    emails.join(', ')
  );
  await input.press('ControlOrMeta+v');
}

/** Type a single email and press Enter to create a tag */
export async function addSingleEmail(page: Page, email: string): Promise<void> {
  const input = getTagInput(page);
  if ((await input.count()) === 0) {
    const fallbackInput = page.locator('input[type="text"]').last();
    await fallbackInput.fill(email);
    await fallbackInput.press('Enter');
    return;
  }
  await input.fill(email);
  await input.press('Enter');
}

/** Get the count of tag pills currently displayed */
export async function getTagCount(page: Page): Promise<number> {
  // Tag pills contain a close icon (material-icons-outlined "close")
  // Each pill is a Flex with a Text + close icon
  return page.locator('span.material-icons-outlined:text("close")').count();
}

/** Get all tag values as text */
export async function getTagValues(page: Page): Promise<string[]> {
  // Each tag pill has a Text element with the value, followed by a close icon
  const pills = page.locator('span.material-icons-outlined:text("close")').locator('..');
  const count = await pills.count();
  const values: string[] = [];
  for (let i = 0; i < count; i++) {
    const parent = pills.nth(i).locator('..');
    const text = await parent.locator('span').first().textContent();
    if (text) values.push(text.trim());
  }
  return values;
}
