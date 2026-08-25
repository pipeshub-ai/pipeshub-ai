/**
 * chat-paste-attachment.spec.ts
 *
 * Covers the paste-to-attachment feature: a large clipboard text paste is
 * intercepted in `chat-input.tsx::handlePaste` and converted into a
 * `pasted-text-<timestamp>.txt` attachment chip (`PastedTextChip`) instead
 * of being inserted inline, mirroring ChatGPT/Claude. All backend calls are
 * intercepted with page.route() — no live server needed.
 */

import { test, expect } from '../fixtures/base.fixture';

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_LLMS_RESPONSE = {
  status: 'success',
  models: [
    {
      modelType: 'chat',
      provider: 'openAI',
      modelName: 'GPT-4o mini',
      modelKey: 'gpt-4o-mini',
      isMultimodal: false,
      isReasoning: false,
      isDefault: true,
      modelFriendlyName: 'GPT-4o mini',
    },
  ],
  message: 'Success',
};

const MOCK_CONVERSATIONS_RESPONSE = {
  conversations: [],
  source: 'owned',
  pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
};

const MOCK_UPLOAD_RESPONSE = {
  attachments: [
    {
      recordId: 'record-paste-001',
      recordName: 'pasted-text-2026-01-01-00-00-00.txt',
      mimeType: 'text/plain',
      extension: 'txt',
      virtualRecordId: 'vrecord-paste-001',
    },
  ],
};

/** ~6,050 characters over 30 lines — above both the 5,000-character and the
 *  10-line threshold, so the conversion is unambiguous. */
const LARGE_TEXT = Array.from({ length: 30 }, (_, i) => `Line ${i + 1}: ${'lorem ipsum '.repeat(16)}`).join('\n');

/** Under both thresholds — should paste inline, not become an attachment. */
const SMALL_TEXT = 'Just a short pasted sentence.';

async function mockBaselineApis(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LLMS_RESPONSE) }),
  );

  await page.route('**/api/v1/conversations*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_CONVERSATIONS_RESPONSE),
      });
    }
    return route.continue();
  });

  await page.route('**/api/v1/conversations/attachments/upload', (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_UPLOAD_RESPONSE),
    });
  });
}

/**
 * Dispatches a synthetic `paste` ClipboardEvent carrying `text` as
 * `text/plain`, targeted at the last textarea on the page. `ClipboardEvent`
 * doesn't expose modifier keys, so the app tracks Shift itself via
 * window-level keydown/keyup — real key events from `page.keyboard` drive
 * that, letting this helper double as the Shift-bypass test too.
 */
async function pasteText(page: import('@playwright/test').Page, text: string) {
  await page.evaluate((pastedText) => {
    const dt = new DataTransfer();
    dt.setData('text/plain', pastedText);
    const textarea = document.querySelectorAll('textarea')[document.querySelectorAll('textarea').length - 1];
    const event = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    textarea.dispatchEvent(event);
  }, text);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Chat — paste-to-attachment', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await page.locator('textarea').last().click();
  });

  test('a large paste is converted into a "Pasted text" chip, not inserted inline', async ({ page }) => {
    await pasteText(page, LARGE_TEXT);

    await expect(page.getByText('Pasted text').first()).toBeVisible({ timeout: 10_000 });
    // The textarea itself must stay empty — the text went to the chip, not inline.
    await expect(page.locator('textarea').last()).toHaveValue('');
  });

  test('a short paste is inserted inline, not converted to a chip', async ({ page }) => {
    await pasteText(page, SMALL_TEXT);

    await expect(page.locator('textarea').last()).toHaveValue(SMALL_TEXT);
    await expect(page.getByText('Pasted text')).toHaveCount(0);
  });

  test('Shift held during paste bypasses the attachment conversion', async ({ page }) => {
    await page.keyboard.down('Shift');
    await pasteText(page, LARGE_TEXT);
    await page.keyboard.up('Shift');

    await expect(page.getByText('Pasted text')).toHaveCount(0);
    await expect(page.locator('textarea').last()).toHaveValue(LARGE_TEXT);
  });

  test('clicking the chip opens a preview dialog with the pasted content', async ({ page }) => {
    await pasteText(page, LARGE_TEXT);
    await expect(page.getByText('Pasted text').first()).toBeVisible({ timeout: 10_000 });

    await page.getByText('Pasted text').first().click();

    await expect(page.getByText(/Line 1:/).first()).toBeVisible({ timeout: 10_000 });
  });

  test('"Show in text field" moves the pasted content back to the textarea and removes the chip', async ({
    page,
  }) => {
    await pasteText(page, LARGE_TEXT);
    await expect(page.getByText('Pasted text').first()).toBeVisible({ timeout: 10_000 });

    // Wait for the mocked upload to resolve — the action only renders once uploaded.
    const showInTextField = page.getByRole('button', { name: /show in text field/i });
    await expect(showInTextField.first()).toBeVisible({ timeout: 10_000 });
    await showInTextField.first().click();

    await expect(page.getByText('Pasted text')).toHaveCount(0);
    await expect(page.locator('textarea').last()).toHaveValue(new RegExp('Line 1:'));
  });

  test('removing the chip deletes the attachment', async ({ page }) => {
    await pasteText(page, LARGE_TEXT);
    await expect(page.getByText('Pasted text').first()).toBeVisible({ timeout: 10_000 });

    const removeBtn = page.getByRole('button', { name: /remove pasted text/i });
    await expect(removeBtn.first()).toBeVisible({ timeout: 10_000 });
    await removeBtn.first().click();

    await expect(page.getByText('Pasted text')).toHaveCount(0);
  });
});
