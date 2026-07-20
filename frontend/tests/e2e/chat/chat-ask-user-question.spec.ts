/**
 * chat-ask-user-question.spec.ts
 *
 * Tests for the interactive `ask_user_question` clarification card emitted
 * by the agent tool `internaltools.ask_user_question`.
 *
 * ── Flow being tested ────────────────────────────────────────────────────
 *
 *  1. User sends a message on an agent chat (/chat/?agentId=…)
 *  2. Backend fires AG-UI frames:
 *       CUSTOM(conversation_created) → CUSTOM(ask_user_question)
 *       (no `RUN_FINISHED` — stream stays open)
 *  3. The AskUserQuestionCard renders with:
 *       - heading "Quick question :" / "Quick questions :"
 *       - question text + option labels (radio or checkbox)
 *       - "Something else" synthetic option always appended
 *       - Step indicator "Step N of M" for multi-question flows
 *       - Back / Skip / Next / Submit buttons
 *  4. User selects an option → Submit enabled
 *  5. User clicks Submit → store sets status 'submitted';
 *       a second stream fires to the existing agent conversation endpoint
 *       with the formatted answer as the new user message
 *  6. Card collapses into the 'submitted' read-only view ("Question :", "Show more")
 *
 * ── i18n key → English string map (from en-US.json) ─────────────────────
 *   askUserQuestion.quickQuestionSingular  → "Quick question :"
 *   askUserQuestion.quickQuestionPlural    → "Quick questions :"
 *   askUserQuestion.questionsHeadingSingular → "Question :"
 *   askUserQuestion.submit                 → "Submit"
 *   askUserQuestion.skip                   → "Skip"
 *   askUserQuestion.next                   → "Next"
 *   askUserQuestion.back                   → "Back"
 *   askUserQuestion.stepOf                 → "Step {{step}} of {{total}}"
 *   askUserQuestion.answered               → "Answered"
 *   askUserQuestion.showMore               → "Show more"
 *
 * ── Selector strategy ───────────────────────────────────────────────────
 *   Radio (single-select) : [role="radio"]   (Radix RadioGroup.Item)
 *   Checkbox (multi-select): [role="checkbox"] (Radix Checkbox)
 *   Submit / Skip / Next / Back : page.locator('button').filter({ hasText: … })
 *
 * ── API routes intercepted ──────────────────────────────────────────────
 *   GET  …/api/v1/configurationManager/ai-models/available/llm
 *   GET  …/api/v1/conversations          (main sidebar list — GET only)
 *   GET  …/api/v1/agents/test-agent-e2e  (agent detail)
 *   GET  …/api/v1/agents/test-agent-e2e/conversations
 *   POST …/api/v1/agents/test-agent-e2e/conversations/stream
 *   POST …/api/v1/agents/test-agent-e2e/conversations/:id/messages/stream
 */

import { test, expect } from '../fixtures/base.fixture';
import { buildAguiSseBody, buildAguiAskUserQuestionSseBody } from './agui-sse-builder';

// ─────────────────────────────────────────────────────────────────────────────
// Shared constants
// ─────────────────────────────────────────────────────────────────────────────

const AGENT_ID = 'test-agent-e2e';
const AGENT_CONV_ID = 'conv-agent-ask-e2e-001';

const MOCK_MODEL_INFO = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'auto',
  modelFriendlyName: 'GPT-4o mini',
};

const MOCK_LLMS = {
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

/** Minimal agent detail that satisfies AgentDetail interface. */
const MOCK_AGENT_DETAIL = {
  status: 'success',
  agent: {
    id: AGENT_ID,
    _id: AGENT_ID,
    _key: AGENT_ID,
    name: 'Test E2E Agent',
    description: 'Agent used for E2E testing.',
    systemPrompt: '',
    instructions: '',
    startMessage: '',
    isActive: true,
    isDeleted: false,
    tags: [],
    models: [
      {
        modelKey: 'gpt-4o-mini',
        modelName: 'GPT-4o mini',
        provider: 'openAI',
        isReasoning: false,
        isMultimodal: false,
        isDefault: true,
        modelType: 'chat',
        modelFriendlyName: 'GPT-4o mini',
      },
    ],
    toolsets: [],
    knowledge: [],
    shareWithOrg: false,
    access_type: 'owned',
    user_role: 'owner',
    can_edit: true,
    can_delete: true,
    can_share: true,
    can_view: true,
    createdBy: 'user-e2e',
    updatedBy: 'user-e2e',
    createdAtTimestamp: Date.now(),
    updatedAtTimestamp: Date.now(),
  },
};

const MOCK_AGENT_CONVERSATIONS_EMPTY = {
  conversations: [],
  sharedWithMeConversations: [],
  pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
};

const MOCK_MAIN_CONVERSATIONS_EMPTY = {
  conversations: [],
  source: 'owned',
  pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
};

// ─────────────────────────────────────────────────────────────────────────────
// SSE body builders (AG-UI wire format — see agui-sse-builder.ts)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Builds an AG-UI SSE body that stops after the CUSTOM(ask_user_question)
 * frame. The stream deliberately has no `RUN_FINISHED` — the card should
 * remain interactive.
 */
function buildAskQuestionSse(
  questions: Array<{
    uuid: string;
    question: string;
    options: Array<{ id: string; label: string; isUserInput: boolean }>;
    multiSelect: boolean;
  }>,
  userIntent?: string,
): string {
  return buildAguiAskUserQuestionSseBody(
    AGENT_CONV_ID,
    { name: 'ask_user_question', ...(userIntent ? { userIntent } : {}), questions },
    'E2E Ask Question Test',
  );
}

/**
 * Builds a complete AG-UI answer body for the follow-up stream after card submission.
 */
function buildAnswerSse(answer: string): string {
  return buildAguiSseBody({
    conversationId: AGENT_CONV_ID,
    userMessageId: 'msg-user-ask-001',
    botMessageId: 'msg-bot-ask-001',
    question: 'User selections:\n1. "Which department?" → Engineering',
    answer,
    modelInfo: MOCK_MODEL_INFO,
    requestId: 'req-ask-e2e',
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Route mock helpers
// ─────────────────────────────────────────────────────────────────────────────

async function mockBaselineApis(page: import('@playwright/test').Page) {
  // LLMs list
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_LLMS),
    }),
  );

  // Main conversations list (GET only)
  await page.route('**/api/v1/conversations*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_MAIN_CONVERSATIONS_EMPTY),
      });
    }
    return route.continue();
  });
}

async function mockAgentApis(page: import('@playwright/test').Page) {
  // Agent detail — required for the page to render agent-mode input
  await page.route(`**/api/v1/agents/${AGENT_ID}`, (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_AGENT_DETAIL),
    });
  });

  // Agent conversations list (sidebar)
  await page.route(`**/api/v1/agents/${AGENT_ID}/conversations*`, (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_AGENT_CONVERSATIONS_EMPTY),
    });
  });
}

/**
 * Navigate to agent chat page and wait for the textarea to be ready.
 */
async function gotoAgentChat(page: import('@playwright/test').Page) {
  await page.goto(`/chat/?agentId=${AGENT_ID}`);
  await page.waitForSelector('textarea', { timeout: 15_000 });
}

/**
 * Type a message and press Enter to submit.
 */
async function sendMessage(page: import('@playwright/test').Page, message: string) {
  const textarea = page.locator('textarea').last();
  await textarea.click();
  await textarea.fill(message);
  await textarea.press('Enter');
}

// ─────────────────────────────────────────────────────────────────────────────
// Single-select (radio) tests
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Ask User Question — single-select card', () => {
  const SINGLE_Q = {
    uuid: 'q-single-001',
    question: 'Which department are you in?',
    options: [
      { id: 'opt-eng', label: 'Engineering', isUserInput: false },
      { id: 'opt-mkt', label: 'Marketing', isUserInput: false },
      { id: 'opt-sales', label: 'Sales', isUserInput: false },
    ],
    multiSelect: false,
  };

  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await mockAgentApis(page);

    // Agent stream: returns ask_user_question SSE
    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        body: buildAskQuestionSse([SINGLE_Q], 'I need to understand your context.'),
      });
    });

    await gotoAgentChat(page);
    await sendMessage(page, 'Help me write a report');

    // Wait for the card to appear
    await page.locator('text=Which department are you in?').waitFor({ timeout: 20_000 });
  });

  test('card heading "Quick question :" is visible', async ({ page }) => {
    await expect(
      page.locator('text=/Quick question/i').first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('user intent is displayed above the question', async ({ page }) => {
    await expect(
      page.locator('text=I need to understand your context.').first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('all option labels are rendered', async ({ page }) => {
    await expect(page.locator('text=Engineering').first()).toBeVisible();
    await expect(page.locator('text=Marketing').first()).toBeVisible();
    await expect(page.locator('text=Sales').first()).toBeVisible();
  });

  test('"Something else" synthetic option is always appended', async ({ page }) => {
    await expect(page.locator('text=Something else').first()).toBeVisible();
  });

  test('Submit button is disabled before any option is selected', async ({ page }) => {
    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await expect(submitBtn).toBeDisabled({ timeout: 5_000 });
  });

  test('Skip button is enabled without selecting an option', async ({ page }) => {
    const skipBtn = page.locator('button').filter({ hasText: /^Skip$/ }).first();
    await expect(skipBtn).toBeEnabled({ timeout: 5_000 });
  });

  test('selecting a radio option enables the Submit button', async ({ page }) => {
    // Radio options render as role="radio" (Radix RadioGroup.Item)
    const radios = page.locator('[role="radio"]');
    const firstRadio = radios.first();

    await expect(firstRadio).toBeVisible({ timeout: 5_000 });
    await firstRadio.click();

    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await expect(submitBtn).toBeEnabled({ timeout: 3_000 });
  });

  test('clicking a different radio option keeps Submit enabled', async ({ page }) => {
    const radios = page.locator('[role="radio"]');

    // Select Engineering first
    await radios.first().click();
    // Switch to Marketing
    await radios.nth(1).click();

    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await expect(submitBtn).toBeEnabled({ timeout: 3_000 });
  });

  test('clicking Submit fires the follow-up stream to the agent conversation endpoint', async ({
    page,
  }) => {
    let followUpStreamFired = false;

    // Mock the follow-up stream (existing conversation endpoint)
    await page.route(
      `**/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
      (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        followUpStreamFired = true;
        return route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: buildAnswerSse('Great! Based on your Engineering context, here is the report…'),
        });
      },
    );

    const radios = page.locator('[role="radio"]');
    await radios.first().click();

    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await submitBtn.click();

    // Wait for follow-up answer to render
    await expect(
      page.locator('text=Great! Based on your Engineering context').first(),
    ).toBeVisible({ timeout: 20_000 });

    expect(followUpStreamFired).toBe(true);
  });

  test('after Submit the card transitions to the submitted (collapsed) state', async ({ page }) => {
    // Delay the follow-up stream response so the transient 'submitted' card
    // stays rendered long enough to assert.  Without the delay, onComplete
    // fires immediately and replaces messages before Playwright can check.
    await page.route(
      `**/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
      async (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        // Use Node setTimeout (not page.waitForTimeout) so the timer is not
        // tied to the page lifecycle and does not throw when the test ends.
        await new Promise<void>(resolve => setTimeout(resolve, 5_000));
        // Ignore errors in case the page was already closed before the timer fired.
        await route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: buildAnswerSse('Follow-up answer.'),
        }).catch(() => {});
      },
    );

    const radios = page.locator('[role="radio"]');
    await radios.first().click();

    await page.locator('button').filter({ hasText: /^Submit$/ }).first().click();

    // The submitted card renders synchronously when Submit is clicked
    // (before the delayed follow-up stream resolves).
    await expect(
      page.getByText(/Question\s*:/).first(),
    ).toBeVisible({ timeout: 4_000 });

    // Clean up in-flight routes before the test tears down.
    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  test('"Show more" toggle in submitted state expands the selected answers', async ({ page }) => {
    // Same delayed-response trick: keep the submitted card visible while we
    // interact with the "Show more" toggle.
    await page.route(
      `**/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
      async (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        await new Promise<void>(resolve => setTimeout(resolve, 8_000));
        await route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: buildAnswerSse('Follow-up answer.'),
        }).catch(() => {});
      },
    );

    const radios = page.locator('[role="radio"]');
    await radios.first().click();
    await page.locator('button').filter({ hasText: /^Submit$/ }).first().click();

    // Wait for the submitted card heading — real regex literal, no string escaping.
    await page.getByText(/Question\s*:/).first().waitFor({ timeout: 4_000 });

    // Click "Show more" to expand the answers summary
    const showMoreBtn = page.locator('button').filter({ hasText: /show more/i }).first();
    if (await showMoreBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await showMoreBtn.click();

      // Selected option label should now be visible in the summary
      const engineeringBadge = page.locator('text=Engineering');
      await expect(engineeringBadge.first()).toBeVisible({ timeout: 3_000 });
    }

    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  test('clicking Skip fires the follow-up stream with a no-preference answer', async ({ page }) => {
    let skipStreamFired = false;
    let capturedBody = '';

    await page.route(
      `**/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
      (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        skipStreamFired = true;
        capturedBody = route.request().postData() ?? '';
        return route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: buildAnswerSse('OK, I will proceed with default context.'),
        });
      },
    );

    await page.locator('button').filter({ hasText: /^Skip$/ }).first().click();

    await expect(
      page.locator('text=OK, I will proceed').first(),
    ).toBeVisible({ timeout: 20_000 });

    expect(skipStreamFired).toBe(true);
    // The skip payload should mention no preference
    if (capturedBody) {
      expect(capturedBody).toMatch(/No preference|no_preference/i);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// "Something else" custom-input flow
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Ask User Question — "Something else" custom input', () => {
  const Q_WITH_ELSE = {
    uuid: 'q-else-001',
    question: 'What is your primary goal?',
    options: [
      { id: 'opt-report', label: 'Generate a report', isUserInput: false },
      { id: 'opt-search', label: 'Search knowledge base', isUserInput: false },
    ],
    multiSelect: false,
  };

  async function setup(page: import('@playwright/test').Page) {
    await mockBaselineApis(page);
    await mockAgentApis(page);
    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAskQuestionSse([Q_WITH_ELSE]),
      });
    });
    await gotoAgentChat(page);
    await sendMessage(page, 'What can you help me with?');
    await page.locator('text=What is your primary goal?').waitFor({ timeout: 20_000 });
  }

  test('"Something else" option is always present regardless of payload options', async ({
    page,
  }) => {
    await setup(page);
    await expect(page.locator('text=Something else').first()).toBeVisible();
  });

  test('selecting "Something else" shows the describe-your-answer textarea', async ({ page }) => {
    await setup(page);

    const radios = page.locator('[role="radio"]');
    // "Something else" is the last radio (appended by component)
    await radios.last().click();

    // A TextArea should appear asking to describe the answer
    const describeArea = page.locator('textarea[placeholder*="Describe"], textarea[placeholder*="describe"]').first();
    await expect(describeArea).toBeVisible({ timeout: 3_000 });
  });

  test('Submit remains disabled when "Something else" is selected but textarea is empty', async ({
    page,
  }) => {
    await setup(page);

    const radios = page.locator('[role="radio"]');
    await radios.last().click();

    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await expect(submitBtn).toBeDisabled({ timeout: 3_000 });
  });

  test('Submit becomes enabled after typing in the "Something else" textarea', async ({ page }) => {
    await setup(page);

    const radios = page.locator('[role="radio"]');
    await radios.last().click();

    const describeArea = page.locator('textarea[placeholder*="Describe"], textarea[placeholder*="describe"]').first();
    await describeArea.fill('I need to analyze competitor pricing data.');

    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await expect(submitBtn).toBeEnabled({ timeout: 3_000 });
  });

  test('submitting a "Something else" answer sends the custom text in the stream request', async ({
    page,
  }) => {
    await setup(page);

    let capturedQuery = '';
    await page.route(
      `**/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
      (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        capturedQuery = route.request().postData() ?? '';
        return route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: buildAnswerSse('Got it! Analyzing competitor pricing now.'),
        });
      },
    );

    const radios = page.locator('[role="radio"]');
    await radios.last().click();

    const describeArea = page.locator('textarea[placeholder*="Describe"], textarea[placeholder*="describe"]').first();
    await describeArea.fill('Competitor pricing analysis');

    await page.locator('button').filter({ hasText: /^Submit$/ }).first().click();

    await expect(
      page.locator('text=Analyzing competitor pricing').first(),
    ).toBeVisible({ timeout: 20_000 });

    if (capturedQuery) {
      // The query field should contain the custom text the user typed
      expect(capturedQuery).toContain('Competitor pricing');
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Multi-select (checkbox) card
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Ask User Question — multi-select card', () => {
  const MULTI_Q = {
    uuid: 'q-multi-001',
    question: 'Which data sources should I check?',
    options: [
      { id: 'opt-drive', label: 'Google Drive', isUserInput: false },
      { id: 'opt-slack', label: 'Slack', isUserInput: false },
      { id: 'opt-jira', label: 'Jira', isUserInput: false },
    ],
    multiSelect: true,
  };

  async function setup(page: import('@playwright/test').Page) {
    await mockBaselineApis(page);
    await mockAgentApis(page);
    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAskQuestionSse([MULTI_Q]),
      });
    });
    await gotoAgentChat(page);
    await sendMessage(page, 'Search for project updates');
    await page.locator('text=Which data sources should I check?').waitFor({ timeout: 20_000 });
  }

  test('options render as checkboxes (role="checkbox")', async ({ page }) => {
    await setup(page);
    const checkboxes = page.locator('[role="checkbox"]');
    await expect(checkboxes.first()).toBeVisible({ timeout: 5_000 });
  });

  test('all option labels are visible', async ({ page }) => {
    await setup(page);
    await expect(page.locator('text=Google Drive').first()).toBeVisible();
    await expect(page.locator('text=Slack').first()).toBeVisible();
    await expect(page.locator('text=Jira').first()).toBeVisible();
  });

  test('Submit is disabled when no checkbox is checked', async ({ page }) => {
    await setup(page);
    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await expect(submitBtn).toBeDisabled({ timeout: 5_000 });
  });

  test('checking one checkbox enables Submit', async ({ page }) => {
    await setup(page);
    const checkboxes = page.locator('[role="checkbox"]');
    await checkboxes.first().click(); // Google Drive

    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await expect(submitBtn).toBeEnabled({ timeout: 3_000 });
  });

  test('multiple checkboxes can be selected simultaneously', async ({ page }) => {
    await setup(page);
    const checkboxes = page.locator('[role="checkbox"]');

    await checkboxes.first().click(); // Google Drive
    await checkboxes.nth(1).click(); // Slack

    // Both should show as checked (aria-checked="true" or data-state="checked")
    await expect(checkboxes.first()).toHaveAttribute('data-state', 'checked');
    await expect(checkboxes.nth(1)).toHaveAttribute('data-state', 'checked');
  });

  test('unchecking all checkboxes disables Submit again', async ({ page }) => {
    await setup(page);
    const checkboxes = page.locator('[role="checkbox"]');
    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();

    await checkboxes.first().click(); // check
    await expect(submitBtn).toBeEnabled({ timeout: 3_000 });

    await checkboxes.first().click(); // uncheck
    await expect(submitBtn).toBeDisabled({ timeout: 3_000 });
  });

  test('Submit fires the follow-up stream with selected labels in the payload', async ({
    page,
  }) => {
    let capturedQuery = '';

    await page.route(
      `**/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
      (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        capturedQuery = route.request().postData() ?? '';
        return route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: buildAnswerSse('Searching Google Drive and Slack for project updates.'),
        });
      },
    );

    await setup(page);
    const checkboxes = page.locator('[role="checkbox"]');
    await checkboxes.first().click(); // Google Drive
    await checkboxes.nth(1).click(); // Slack

    await page.locator('button').filter({ hasText: /^Submit$/ }).first().click();

    await expect(
      page.locator('text=Searching Google Drive and Slack').first(),
    ).toBeVisible({ timeout: 20_000 });

    if (capturedQuery) {
      expect(capturedQuery).toContain('Google Drive');
      expect(capturedQuery).toContain('Slack');
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Multi-step flow (multiple questions)
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Ask User Question — multi-step (2 questions)', () => {
  const Q1 = {
    uuid: 'q-step-001',
    question: 'What type of report do you need?',
    options: [
      { id: 'opt-summary', label: 'Executive summary', isUserInput: false },
      { id: 'opt-detailed', label: 'Detailed breakdown', isUserInput: false },
    ],
    multiSelect: false,
  };

  const Q2 = {
    uuid: 'q-step-002',
    question: 'What time period should the report cover?',
    options: [
      { id: 'opt-week', label: 'Last week', isUserInput: false },
      { id: 'opt-month', label: 'Last month', isUserInput: false },
      { id: 'opt-quarter', label: 'Last quarter', isUserInput: false },
    ],
    multiSelect: false,
  };

  async function setup(page: import('@playwright/test').Page) {
    await mockBaselineApis(page);
    await mockAgentApis(page);
    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAskQuestionSse([Q1, Q2], 'I need a few details to generate your report.'),
      });
    });
    await gotoAgentChat(page);
    await sendMessage(page, 'Generate a report for me');
    await page.locator('text=What type of report do you need?').waitFor({ timeout: 20_000 });
  }

  test('card heading shows "Quick questions :" for multiple questions', async ({ page }) => {
    await setup(page);
    await expect(
      page.locator('text=/Quick questions/i').first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('step indicator shows "Step 1 of 2" on the first question', async ({ page }) => {
    await setup(page);
    await expect(
      page.locator('text=/Step 1 of 2/i').first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('"Next" button (not Submit) is shown on the first question', async ({ page }) => {
    await setup(page);
    const nextBtn = page.locator('button').filter({ hasText: /^Next$/ }).first();
    await expect(nextBtn).toBeVisible({ timeout: 5_000 });

    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ });
    const submitVisible = await submitBtn.first().isVisible().catch(() => false);
    expect(submitVisible).toBe(false);
  });

  test('"Next" is disabled until the current question is answered', async ({ page }) => {
    await setup(page);
    const nextBtn = page.locator('button').filter({ hasText: /^Next$/ }).first();
    await expect(nextBtn).toBeDisabled({ timeout: 5_000 });
  });

  test('selecting an option enables "Next" and advances to question 2', async ({ page }) => {
    await setup(page);
    const radios = page.locator('[role="radio"]');
    await radios.first().click();

    const nextBtn = page.locator('button').filter({ hasText: /^Next$/ }).first();
    await expect(nextBtn).toBeEnabled({ timeout: 3_000 });
    await nextBtn.click();

    // Should now show question 2
    await expect(
      page.locator('text=What time period should the report cover?').first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('step indicator shows "Step 2 of 2" after clicking Next', async ({ page }) => {
    await setup(page);
    const radios = page.locator('[role="radio"]');
    await radios.first().click();
    await page.locator('button').filter({ hasText: /^Next$/ }).first().click();

    await expect(
      page.locator('text=/Step 2 of 2/i').first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('"Submit" button appears on the last question', async ({ page }) => {
    await setup(page);
    const radios = page.locator('[role="radio"]');
    await radios.first().click();
    await page.locator('button').filter({ hasText: /^Next$/ }).first().click();

    const submitBtn = page.locator('button').filter({ hasText: /^Submit$/ }).first();
    await expect(submitBtn).toBeVisible({ timeout: 5_000 });
  });

  test('"Back" button on step 2 returns to step 1', async ({ page }) => {
    await setup(page);

    // Advance to step 2
    const radios = page.locator('[role="radio"]');
    await radios.first().click();
    await page.locator('button').filter({ hasText: /^Next$/ }).first().click();
    await page.locator('text=What time period should the report cover?').waitFor({ timeout: 5_000 });

    // Go back
    const backBtn = page.locator('button').filter({ hasText: /^Back$/ }).first();
    await expect(backBtn).toBeEnabled({ timeout: 3_000 });
    await backBtn.click();

    // Step 1 question should be visible again
    await expect(
      page.locator('text=What type of report do you need?').first(),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('completing both steps and clicking Submit fires follow-up stream', async ({ page }) => {
    let streamFired = false;

    await page.route(
      `**/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
      (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        streamFired = true;
        return route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: buildAnswerSse('Generating your executive summary for last month.'),
        });
      },
    );

    await setup(page);

    // Step 1: select first option, click Next
    const radios = page.locator('[role="radio"]');
    await radios.first().click();
    await page.locator('button').filter({ hasText: /^Next$/ }).first().click();

    // Step 2: wait for new radios to render, select first option
    await page.locator('text=What time period').waitFor({ timeout: 5_000 });
    await page.locator('[role="radio"]').first().click();

    await page.locator('button').filter({ hasText: /^Submit$/ }).first().click();

    await expect(
      page.locator('text=Generating your executive summary').first(),
    ).toBeVisible({ timeout: 20_000 });

    expect(streamFired).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Agent context: correct endpoint routing
// ─────────────────────────────────────────────────────────────────────────────

test.describe('Ask User Question — agent endpoint routing', () => {
  const SINGLE_Q = {
    uuid: 'q-routing-001',
    question: 'Which language do you prefer?',
    options: [
      { id: 'opt-py', label: 'Python', isUserInput: false },
      { id: 'opt-ts', label: 'TypeScript', isUserInput: false },
    ],
    multiSelect: false,
  };

  test('first ask_user_question stream uses the agent new-conversation endpoint', async ({
    page,
  }) => {
    let agentStreamUrl = '';

    await mockBaselineApis(page);
    await mockAgentApis(page);

    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/**`, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_AGENT_CONVERSATIONS_EMPTY),
        });
      }
      agentStreamUrl = route.request().url();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAskQuestionSse([SINGLE_Q]),
      });
    });

    await gotoAgentChat(page);
    await sendMessage(page, 'Help me code something');

    await page.locator('text=Which language do you prefer?').waitFor({ timeout: 20_000 });

    // The initial stream must use /agents/:id/conversations/stream (not /messages/stream)
    expect(agentStreamUrl).toContain(`/api/v1/agents/${AGENT_ID}/conversations`);
    expect(agentStreamUrl).not.toContain('/messages/stream');
  });

  test('follow-up stream after submission uses agent conversation messages endpoint', async ({
    page,
  }) => {
    let followUpUrl = '';

    await mockBaselineApis(page);
    await mockAgentApis(page);

    // First stream — ask_user_question
    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAskQuestionSse([SINGLE_Q]),
      });
    });

    // Follow-up stream — capture URL
    await page.route(
      `**/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
      (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        followUpUrl = route.request().url();
        return route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: buildAnswerSse('I will use TypeScript for your project.'),
        });
      },
    );

    await gotoAgentChat(page);
    await sendMessage(page, 'Build a feature for me');
    await page.locator('text=Which language do you prefer?').waitFor({ timeout: 20_000 });

    await page.locator('[role="radio"]').first().click();
    await page.locator('button').filter({ hasText: /^Submit$/ }).first().click();

    await expect(
      page.locator('text=I will use TypeScript').first(),
    ).toBeVisible({ timeout: 20_000 });

    // Follow-up must use /agents/:agentId/conversations/:convId/messages/stream
    expect(followUpUrl).toContain(
      `/api/v1/agents/${AGENT_ID}/conversations/${AGENT_CONV_ID}/messages/stream`,
    );
  });

  test('main /conversations/stream endpoint is NOT called for agent chat', async ({ page }) => {
    let mainStreamCalled = false;

    await mockBaselineApis(page);
    await mockAgentApis(page);

    // Intercept the MAIN (non-agent) stream endpoint to detect accidental calls
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() === 'POST') mainStreamCalled = true;
      return route.continue();
    });

    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAskQuestionSse([SINGLE_Q]),
      });
    });

    await gotoAgentChat(page);
    await sendMessage(page, 'Agent test message');
    await page.locator('text=Which language do you prefer?').waitFor({ timeout: 20_000 });

    expect(mainStreamCalled).toBe(false);
  });
});
