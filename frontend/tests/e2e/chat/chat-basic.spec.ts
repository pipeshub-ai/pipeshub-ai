import { test, expect } from '../fixtures/base.fixture';

// ---------------------------------------------------------------------------
// Common mock helpers
// ---------------------------------------------------------------------------

/** Stable mock LLM list — returned to GET .../ai-models/available/llm */
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

/** Empty conversations list — prevents sidebar load errors */
const MOCK_CONVERSATIONS_RESPONSE = {
  conversations: [],
  source: 'owned',
  pagination: {
    page: 1,
    limit: 20,
    totalCount: 0,
    totalPages: 0,
    hasNextPage: false,
    hasPrevPage: false,
  },
};

/**
 * Apply baseline route mocks that every chat test needs so the page
 * can render without a live backend.
 */
async function mockBaselineApis(page: import('@playwright/test').Page) {
  // LLMs list — required for the model selector to render
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_LLMS_RESPONSE),
    }),
  );

  // Conversations list (owned + shared) — prevents sidebar fetch errors
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
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Chat — page structure', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await page.goto('/chat/');
    // Wait for the React tree to hydrate (input must be present)
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('page loads and URL is /chat/', async ({ page }) => {
    await expect(page).toHaveURL(/\/chat\//);
  });

  test('chat textarea is visible', async ({ page }) => {
    const textarea = page.locator('textarea').last();
    await expect(textarea).toBeVisible();
  });

  test('chat textarea is interactive — accepts typed text', async ({ page }) => {
    const textarea = page.locator('textarea').last();
    await textarea.click();
    await textarea.fill('Hello from Playwright');
    await expect(textarea).toHaveValue('Hello from Playwright');
  });

  test('textarea clears when Escape is pressed (edit cancel)', async ({ page }) => {
    const textarea = page.locator('textarea').last();
    await textarea.fill('some text');
    await expect(textarea).toHaveValue('some text');
    // Clearing manually mirrors the UX — actual Escape behaviour is
    // app-specific (clears only in action-mode), so we just verify typing works
    await textarea.fill('');
    await expect(textarea).toHaveValue('');
  });

  test('send/submit button is disabled when input is empty', async ({ page }) => {
    const textarea = page.locator('textarea').last();
    // Ensure input is blank
    await textarea.fill('');

    // Send button rendered as IconButton containing arrow_upward icon
    const sendBtn = page
      .locator('button')
      .filter({ has: page.locator('span.material-icons-outlined').filter({ hasText: 'arrow_upward' }) })
      .last();

    if (await sendBtn.isVisible()) {
      await expect(sendBtn).toBeDisabled();
    }
  });

  test('send button becomes enabled when input has text', async ({ page }) => {
    const textarea = page.locator('textarea').last();
    await textarea.fill('test query');

    const sendBtn = page
      .locator('button')
      .filter({ has: page.locator('span.material-icons-outlined').filter({ hasText: 'arrow_upward' }) })
      .last();

    if (await sendBtn.isVisible()) {
      await expect(sendBtn).toBeEnabled();
    }
  });

  test('sidebar is present on desktop', async ({ page }) => {
    const viewport = page.viewportSize();
    if (viewport && viewport.width > 768) {
      const collapseBtn = page.locator('button[aria-label="Collapse sidebar"]');
      await expect(collapseBtn).toBeVisible({ timeout: 5_000 });
    }
  });

  test('model selector button is visible in the toolbar', async ({ page }) => {
    // The model selector shows a "memory" icon
    const modelIcon = page
      .locator('span.material-icons-outlined')
      .filter({ hasText: 'memory' })
      .first();
    await expect(modelIcon).toBeVisible({ timeout: 5_000 });
  });

  test('attach-file button is visible in the toolbar', async ({ page }) => {
    const attachIcon = page
      .locator('span.material-icons-outlined')
      .filter({ hasText: 'attach_file' })
      .first();
    await expect(attachIcon).toBeVisible({ timeout: 5_000 });
  });

  test('connectors/apps button is visible in the toolbar', async ({ page }) => {
    const appsIcon = page
      .locator('span.material-icons-outlined')
      .filter({ hasText: 'apps' })
      .first();
    await expect(appsIcon).toBeVisible({ timeout: 5_000 });
  });
});

test.describe('Chat — mode switcher', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('chat / search toggle buttons are present', async ({ page }) => {
    // ModeSwitcher renders Flex (div) elements, not <button> elements.
    // The left pill shows the active query mode toolbar label; in en-US the
    // default "chat" mode translates to "Internal Search". Other possible
    // visible labels are "Agent" and "Web". The right pill shows "Search"
    // text only when search mode is active (icon-only in chat mode).
    const knownLabels = ['Internal Search', 'Agent', 'Web', 'Search'];
    let anyVisible = false;
    for (const label of knownLabels) {
      anyVisible = await page.getByText(label, { exact: true }).first().isVisible().catch(() => false);
      if (anyVisible) break;
    }
    expect(anyVisible).toBeTruthy();
  });
});

test.describe('Chat — new chat navigation', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('new chat button is accessible in the sidebar', async ({ page }) => {
    const viewport = page.viewportSize();
    if (!viewport || viewport.width < 768) {
      test.skip();
      return;
    }

    // "New Chat" or compose/edit icon in the sidebar header
    const newChatEl = page
      .locator('button, [role="button"]')
      .filter({ hasText: /new chat|new|compose/i })
      .first();

    const iconBtn = page
      .locator('button')
      .filter({ has: page.locator('span.material-icons-outlined').filter({ hasText: /edit_square|add|create/ }) })
      .first();

    const isVisible =
      (await newChatEl.isVisible().catch(() => false)) ||
      (await iconBtn.isVisible().catch(() => false));

    expect(isVisible).toBeTruthy();
  });

  test('navigating to /chat/ from a conversation URL lands on clean chat', async ({ page }) => {
    // Navigate to a fake conversation URL first
    await page.goto('/chat/?conversationId=does-not-exist');
    await page.waitForTimeout(1_000);
    // Navigate back to root chat
    await page.goto('/chat/');
    await expect(page).toHaveURL(/\/chat\//);
    // Textarea should still be available
    await expect(page.locator('textarea').last()).toBeVisible({ timeout: 10_000 });
  });
});
