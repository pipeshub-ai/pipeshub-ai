/**
 * chat-message-layout.spec.ts
 *
 * Regression coverage for the industry-standard conversational layout
 * (`UserMessage` / `AssistantMessage` / `ResponseTabs` — see the "Chat UI
 * Redesign" plan). Verifies that:
 *   1. User and assistant turns render as visually separate message blocks.
 *   2. The Response / Sources / Citations tab bar surfaces cited records.
 *   3. An inline `[N]` citation badge in the answer opens its popover.
 *
 * Same AG-UI wire format as `chat-streaming.spec.ts`
 * (CUSTOM(conversation_created) -> TEXT_MESSAGE_START -> TEXT_MESSAGE_CONTENT
 * -> TEXT_MESSAGE_END -> RUN_FINISHED), extended with a `citations` entry on
 * the `bot_response` message so `buildCitationMapsFromApi` (see
 * `response-tabs/citations/utils.ts`) has something to build from.
 */

import { test, expect } from '../fixtures/base.fixture';
import { buildAguiSseBody } from './agui-sse-builder';

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const CONV_ID = 'conv-e2e-layout-001';
const MSG_USER_ID = 'msg-user-e2e-layout-001';
const MSG_BOT_ID = 'msg-bot-e2e-layout-001';
const RECORD_ID = 'record-e2e-layout-001';
const RECORD_NAME = 'Refund Policy.pdf';

const QUESTION = 'What is the refund policy?';
// `[1](url)` is stripped to `[1]` by `processMarkdownContent` and rendered as
// an inline citation badge resolved against `citationsOrder[1]`.
const ANSWER = `Refunds are processed within 5 business days [1](https://example.com/${RECORD_ID}).`;

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

const MOCK_MODEL_INFO = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'internal_search',
  modelFriendlyName: 'GPT-4o mini',
};

const MOCK_CONVERSATIONS_EMPTY = {
  conversations: [],
  source: 'owned',
  pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
};

/** One citation, shaped like `CitationApiResponse` (see `citations/utils.ts::buildCitationMapsFromApi`). */
const MOCK_CITATION = {
  citationId: 'citation-e2e-001',
  citationData: {
    content: 'Refunds are processed within 5 business days of the return request.',
    chunkIndex: 1,
    citationType: 'vectordb|document',
    updatedAt: new Date().toISOString(),
    metadata: {
      recordId: RECORD_ID,
      recordName: RECORD_NAME,
      connector: 'GOOGLE_DRIVE',
      recordType: 'FILE',
      webUrl: `https://example.com/${RECORD_ID}`,
      mimeType: 'application/pdf',
      extension: 'pdf',
      previewRenderable: true,
      hideWeburl: false,
    },
  },
};

/**
 * Same AG-UI happy-path sequence as `buildAguiSseBody`, but with a citation
 * attached to the `bot_response` message so the Sources / Citations tabs
 * have content to render.
 */
function buildSseBodyWithCitation(): string {
  const base = buildAguiSseBody({
    conversationId: CONV_ID,
    userMessageId: MSG_USER_ID,
    botMessageId: MSG_BOT_ID,
    question: QUESTION,
    answer: ANSWER,
    modelInfo: MOCK_MODEL_INFO,
    requestId: 'req-e2e-layout-001',
  });

  // `buildAguiSseBody` always emits an empty `citations: []` on the bot
  // message — inject our mock citation into that specific JSON array rather
  // than re-implementing the whole frame builder.
  return base.replace(
    `"_id":"${MSG_BOT_ID}","messageType":"bot_response","content":${JSON.stringify(ANSWER)},"contentFormat":"MARKDOWN","citations":[]`,
    `"_id":"${MSG_BOT_ID}","messageType":"bot_response","content":${JSON.stringify(ANSWER)},"contentFormat":"MARKDOWN","citations":${JSON.stringify([MOCK_CITATION])}`,
  );
}

async function mockBaselineApis(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_LLMS),
    }),
  );

  await page.route('**/api/v1/conversations*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_CONVERSATIONS_EMPTY),
      });
    }
    return route.continue();
  });
}

async function mockStreamEndpoint(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/conversations/stream', (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    return route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
      },
      body: buildSseBodyWithCitation(),
    });
  });
}

async function sendMessage(page: import('@playwright/test').Page, message: string) {
  const textarea = page.locator('textarea').last();
  await textarea.click();
  await textarea.fill(message);
  await textarea.press('Enter');
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Chat — message layout (user/assistant separation, sources)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await mockStreamEndpoint(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('user message and assistant answer both render, as separate blocks', async ({ page }) => {
    await sendMessage(page, QUESTION);

    const userBlock = page.locator(`text=${QUESTION}`).first();
    await expect(userBlock).toBeVisible({ timeout: 10_000 });

    const answerBlock = page.locator('text=/Refunds are processed within 5 business days/').first();
    await expect(answerBlock).toBeVisible({ timeout: 20_000 });

    // The two turns must be genuinely distinct DOM nodes — this is a
    // regression guard against the old layout, which rendered the question
    // as a heading *inside* the same response block as the answer.
    const userBox = await userBlock.evaluate((el) => el.getBoundingClientRect());
    const answerBox = await answerBlock.evaluate((el) => el.getBoundingClientRect());
    expect(userBox.top).toBeLessThan(answerBox.top);
  });

  test('the Sources tab shows a badge count and, when clicked, reveals the cited record', async ({ page }) => {
    await sendMessage(page, QUESTION);
    await expect(page.locator('text=/Refunds are processed/').first()).toBeVisible({ timeout: 20_000 });

    // The "Sources" tab should be visible in the tab bar with a count badge.
    const sourcesTab = page.locator('text=/Sources/').first();
    await expect(sourcesTab).toBeVisible({ timeout: 10_000 });

    // The record name is not visible while the "Answer" tab is active.
    await expect(page.locator(`text=${RECORD_NAME}`)).not.toBeVisible();

    // Clicking the Sources tab reveals the cited record.
    await sourcesTab.click();
    await expect(page.locator(`text=${RECORD_NAME}`).first()).toBeVisible({ timeout: 5_000 });
  });

  test('clicking an inline citation badge opens its popover', async ({ page }) => {
    await sendMessage(page, QUESTION);
    await expect(page.locator('text=/Refunds are processed/').first()).toBeVisible({ timeout: 20_000 });

    // `CitationNumberCircle` renders as a `<button>`/`<a>` with
    // `aria-haspopup="dialog"` — the stable hook for the inline badge trigger.
    const citationTrigger = page.locator('[aria-haspopup="dialog"]').first();
    await expect(citationTrigger).toBeVisible({ timeout: 10_000 });
    await citationTrigger.click();

    // The popover renders the same record name as the source card.
    await expect(page.locator(`text=${RECORD_NAME}`).first()).toBeVisible({ timeout: 5_000 });
    await expect(citationTrigger).toHaveAttribute('aria-expanded', 'true');
  });
});
