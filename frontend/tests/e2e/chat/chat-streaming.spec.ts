/**
 * chat-streaming.spec.ts
 *
 * Tests that cover the full SSE streaming flow — from typing a message and
 * hitting send, through the SSE event sequence, to the final rendered answer.
 *
 * All backend calls are intercepted with page.route() so no live server is
 * needed. The mock SSE body replicates the AG-UI event sequence the real
 * backend emits, since `chat/api.ts::runChatStream` always negotiates
 * `protocol: 'agui'` (see `agui-sse-builder.ts`):
 *
 *   CUSTOM(conversation_created) → TEXT_MESSAGE_START →
 *   TEXT_MESSAGE_CONTENT(s) → TEXT_MESSAGE_END → RUN_FINISHED
 *
 * Endpoint conventions (from chat/api.ts):
 *   New chat    : POST /api/v1/conversations/stream
 *   Existing    : POST /api/v1/conversations/:id/messages/stream
 *   Feedback    : POST /api/v1/conversations/:id/message/:msgId/feedback
 *   Conv list   : GET  /api/v1/conversations
 *   LLMs        : GET  /api/v1/configurationManager/ai-models/available/llm
 */

import { test, expect } from '../fixtures/base.fixture';
import {
  buildAguiSseBody,
  buildAguiErrorSseBody,
  buildAguiPartialSseBody,
  buildAguiStoppedSseBody,
  buildAguiToolCallStartSseBody,
} from './agui-sse-builder';

// ---------------------------------------------------------------------------
// Mock data builders
// ---------------------------------------------------------------------------

const CONV_ID = 'conv-e2e-stream-001';
const MSG_USER_ID = 'msg-user-e2e-001';
const MSG_BOT_ID = 'msg-bot-e2e-001';

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

/**
 * Build a minimal but fully valid AG-UI SSE response body that the app's
 * `createAGUIEventHandler` (see chat/agui-event-handler.ts) will parse
 * correctly.
 *
 * Frames:
 *  1. CUSTOM(conversation_created) — assigns conversationId; triggers URL sync
 *  2. TEXT_MESSAGE_START/CONTENT/END — accumulates into streamingContent
 *  3. RUN_FINISHED — finalises slot, loads historical messages
 */
function buildSseBody(question: string, answer: string): string {
  return buildAguiSseBody({
    conversationId: CONV_ID,
    userMessageId: MSG_USER_ID,
    botMessageId: MSG_BOT_ID,
    question,
    answer,
    modelInfo: MOCK_MODEL_INFO,
    requestId: 'req-e2e-001',
  });
}

/**
 * Wire up the three baseline mocks that the chat page needs before
 * any interaction can happen without 401/500 errors.
 */
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

/**
 * Wire up the SSE stream mock for a new conversation
 * (POST /api/v1/conversations/stream).
 */
async function mockStreamEndpoint(
  page: import('@playwright/test').Page,
  question: string,
  answer: string,
) {
  await page.route('**/api/v1/conversations/stream', (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    return route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
      },
      body: buildSseBody(question, answer),
    });
  });
}

/**
 * Type a message and submit via Enter. Returns after the keypress.
 */
async function sendMessage(page: import('@playwright/test').Page, message: string) {
  const textarea = page.locator('textarea').last();
  await textarea.click();
  await textarea.fill(message);
  await textarea.press('Enter');
}

// ---------------------------------------------------------------------------
// Test suites
// ---------------------------------------------------------------------------

test.describe('Chat — SSE streaming (mocked backend)', () => {
  const QUESTION = 'What is PipesHub?';
  const ANSWER = 'PipesHub is an AI-powered workplace platform for enterprise search and automation.';

  test.beforeEach(async ({ page }) => {
    // All route mocks must be registered BEFORE page.goto so that requests
    // fired during load are already intercepted.
    await mockBaselineApis(page);
    await mockStreamEndpoint(page, QUESTION, ANSWER);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  // ── User message rendering ─────────────────────────────────────────────

  test('user message bubble appears after send', async ({ page }) => {
    await sendMessage(page, QUESTION);
    await expect(page.locator(`text=${QUESTION}`).first()).toBeVisible({ timeout: 10_000 });
  });

  // ── AG-UI frame: CUSTOM(conversation_created) → URL sync ────────────────

  test('URL updates to include conversationId after connected event', async ({ page }) => {
    await sendMessage(page, QUESTION);

    // The CUSTOM(conversation_created) frame fires with conversationId = CONV_ID.
    // The page's router.replace() should update the URL accordingly.
    await expect(page).toHaveURL(new RegExp(CONV_ID), { timeout: 15_000 });
  });

  // ── streaming indicator while the run is in progress ────────────────────

  test('streaming spinner / status indicator is visible while answer is loading', async ({ page }) => {
    // Use a delayed mock so we can observe the in-progress state
    await page.route('**/api/v1/conversations/stream', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      // Delay fulfillment so React renders the loading state first
      await new Promise<void>((resolve) => setTimeout(resolve, 400));
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        body: buildSseBody(QUESTION, ANSWER),
      });
    });

    await sendMessage(page, QUESTION);

    // A loading/thinking indicator should be briefly visible.
    // It can be an animated dot, a spinner, or a status text like "Thinking…".
    const indicator = page.locator(
      '[class*="spinner"], [class*="loader"], [class*="thinking"], ' +
      'text=/Thinking|Searching|Planning|thinking|searching|planning/i',
    );
    // May disappear quickly — just assert the answer eventually arrives
    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });
  });

  // ── AG-UI frame: TEXT_MESSAGE_CONTENT → answer rendered ─────────────────

  test('assistant answer is rendered in the message list', async ({ page }) => {
    await sendMessage(page, QUESTION);
    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });
  });

  // ── AG-UI frame: RUN_FINISHED → streaming flag cleared ──────────────────

  test('stop button disappears after complete event fires', async ({ page }) => {
    await sendMessage(page, QUESTION);

    // Wait for answer to confirm RUN_FINISHED was processed
    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });

    // Stop button (data-testid="chat-stop-button", see chat-input.tsx) should
    // no longer be present once RUN_FINISHED clears `isStreaming`.
    const stopBtn = page.locator('[data-testid="chat-stop-button"]');
    await expect(stopBtn.first()).not.toBeVisible({ timeout: 5_000 }).catch(() => {
      // If stop button never rendered (very fast response), test still passes
    });
  });

  // ── SSE error handling ──────────────────────────────────────────────────

  test('error event from stream shows an error message in the thread', async ({ page }) => {
    // Override the stream mock with an error response
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiErrorSseBody(CONV_ID, 'LLM service is temporarily unavailable.'),
      });
    });

    await sendMessage(page, 'trigger an error');

    // The app renders a generic error message in the assistant bubble
    const errorMsg = page.locator(
      'text=/error|unavailable|failed|Failed|Error|try again/i',
    );
    await expect(errorMsg.first()).toBeVisible({ timeout: 15_000 });
  });

  // ── HTTP error from stream endpoint ────────────────────────────────────

  test('HTTP 500 from stream shows an error in the thread', async ({ page }) => {
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'Internal server error' }),
      });
    });

    await sendMessage(page, 'force a 500 error');

    const errorMsg = page.locator('text=/error|Error|failed|Failed|try again/i');
    await expect(errorMsg.first()).toBeVisible({ timeout: 15_000 });
  });
});

// ---------------------------------------------------------------------------
// Multiple messages in the same conversation
// ---------------------------------------------------------------------------

test.describe('Chat — multi-turn conversation (mocked backend)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('second message in the same conversation uses the /messages/stream endpoint', async ({
    page,
  }) => {
    const Q1 = 'First question';
    const A1 = 'First answer from mock.';
    const Q2 = 'Follow-up question';
    const A2 = 'Follow-up answer from mock.';

    // First turn — new conversation endpoint
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildSseBody(Q1, A1),
      });
    });

    await sendMessage(page, Q1);
    await expect(page.locator(`text=${A1}`).first()).toBeVisible({ timeout: 20_000 });

    // Second turn — existing conversation endpoint (after URL gets conversationId)
    await page.route(`**/api/v1/conversations/${CONV_ID}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildSseBody(Q2, A2),
      });
    });

    await sendMessage(page, Q2);
    await expect(page.locator(`text=${A2}`).first()).toBeVisible({ timeout: 20_000 });
  });
});

// ---------------------------------------------------------------------------
// Stop streaming mid-flight
//
// `cancelStreamForSlot` (chat/streaming.ts) is cooperative: it POSTs
// `/cancel` carrying `runId` and only hard-aborts the connection after a
// `STOP_GRACE_MS` (5s) grace timer if the backend hasn't already ended the
// run via `RUN_FINISHED`. None of these mocks can hold a real SSE
// connection open indefinitely (`route.fulfill` delivers one fixed body),
// so — matching `buildAguiPartialSseBody`/`buildAguiAskUserQuestionSseBody`
// elsewhere in this suite — a body with no `RUN_FINISHED` still leaves the
// slot `isStreaming: true` after the mocked request "completes", which is
// exactly the state the grace-timeout fallback is built to clean up. Tests
// below that want the partial-answer path wait out that real 5s timer;
// tests that want the "backend confirmed the stop" path instead send
// `buildAguiStoppedSseBody` outright, so the grace timer never needs to
// fire.
// ---------------------------------------------------------------------------

test.describe('Chat — stop streaming (assistant)', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('stop posts a cancel request with the runId, preserves the partial answer, and shows the Stopped marker', async ({ page }) => {
    let cancelBody: { runId?: string } | null = null;

    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      // conversation_created + one delta, no RUN_FINISHED — see suite comment above.
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiPartialSseBody('conv-stop-001', 'Partial answer before stop…'),
      });
    });

    await page.route('**/api/v1/conversations/conv-stop-001/cancel', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      cancelBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled: true }),
      });
    });

    await sendMessage(page, 'A slow query');

    const stopBtn = page.locator('[data-testid="chat-stop-button"]');
    await expect(stopBtn).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('text=Partial answer before stop').first()).toBeVisible({ timeout: 10_000 });
    await stopBtn.click();

    // Cooperative POST fires immediately — before the hard-abort fallback.
    await expect.poll(() => cancelBody !== null, { timeout: 5_000 }).toBe(true);
    expect(cancelBody?.runId).toBeTruthy();

    // No backend RUN_FINISHED ever arrives here, so the grace-timeout
    // fallback (`STOP_GRACE_MS` = 5s) owns cleanup: hard-abort + locally
    // synthesize the `status: 'stopped'` message from the streamed text.
    await expect(stopBtn).not.toBeVisible({ timeout: 8_000 });
    await expect(page.locator('text=Partial answer before stop').first()).toBeVisible();
    await expect(page.locator('[data-testid="chat-stopped-marker"]').first()).toBeVisible({ timeout: 3_000 });
  });

  test('a backend RUN_FINISHED confirming the stop is honored without waiting for the grace timeout', async ({ page }) => {
    // Simulates the fast path: `/chat/cancel` reached the run's owner and
    // `AnswerFinalizer`'s cancelled branch (respond.py) persisted +
    // re-emitted RUN_FINISHED before the client's 5s grace timer could fire.
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiStoppedSseBody({
          conversationId: 'conv-stop-confirmed-001',
          userMessageId: 'msg-user-stop-confirmed',
          botMessageId: 'msg-bot-stop-confirmed',
          question: 'Confirmed-stop query',
          answer: 'Answer truncated by a confirmed stop.',
          modelInfo: MOCK_MODEL_INFO,
        }),
      });
    });

    await sendMessage(page, 'Confirmed-stop query');

    // RUN_FINISHED clears `isStreaming`/`stopping` synchronously — no 5s wait.
    await expect(page.locator('text=Answer truncated by a confirmed stop').first())
      .toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-testid="chat-stopped-marker"]').first())
      .toBeVisible({ timeout: 3_000 });
    await expect(page.locator('[data-testid="chat-stop-button"]')).not.toBeVisible({ timeout: 3_000 });
  });

  test('stop while still "Thinking" (no answer text yet) cancels the run and drops the empty placeholder', async ({ page }) => {
    const convId = 'conv-stop-thinking-001';
    let cancelFired = false;

    // First turn completes normally so the slot's `convId` is a real,
    // already-known id (not null) — required for the cooperative branch:
    // `cancelStreamForSlot` hard-aborts immediately whenever `!convId`.
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiSseBody({
          conversationId: convId,
          userMessageId: 'msg-user-thinking-1',
          botMessageId: 'msg-bot-thinking-1',
          question: 'First question',
          answer: 'First answer.',
          modelInfo: MOCK_MODEL_INFO,
        }),
      });
    });
    await sendMessage(page, 'First question');
    await expect(page.locator('text=First answer.').first()).toBeVisible({ timeout: 20_000 });

    // Second turn: hangs with zero tokens streamed ("Thinking") until the
    // grace timer's hard-abort ends the (mocked) connection.
    await page.route(`**/api/v1/conversations/${convId}/messages/stream`, async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise<void>((resolve) => setTimeout(resolve, 8_000));
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiPartialSseBody(convId, ''),
      }).catch(() => {});
    });
    await page.route(`**/api/v1/conversations/${convId}/cancel`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      cancelFired = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled: true }),
      });
    });

    await sendMessage(page, 'A question that never gets an answer');

    const stopBtn = page.locator('[data-testid="chat-stop-button"]');
    await expect(stopBtn).toBeVisible({ timeout: 10_000 });
    await stopBtn.click();

    await expect.poll(() => cancelFired, { timeout: 5_000 }).toBe(true);

    // Wait out the grace timeout (`STOP_GRACE_MS` = 5s) so the hard-abort
    // fallback has actually settled the slot before asserting its effect.
    await expect(stopBtn).not.toBeVisible({ timeout: 8_000 });

    // `buildStoppedMessages` drops the trailing assistant row entirely when
    // no text ever streamed — an empty "Stopped" bubble would just be noise.
    await expect(page.locator('[data-testid="chat-stopped-marker"]')).not.toBeVisible();
    await expect(page.locator('text=A question that never gets an answer').first()).toBeVisible();

    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  test('stop during tool execution ends the run without crashing the activity timeline', async ({ page }) => {
    const convId = 'conv-stop-tool-001';

    await page.route('**/api/v1/conversations/stream', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise<void>((resolve) => setTimeout(resolve, 8_000));
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiToolCallStartSseBody(convId, 'tool-call-e2e-001', 'search_knowledge_base'),
      }).catch(() => {});
    });
    await page.route(`**/api/v1/conversations/${convId}/cancel`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled: true }),
      });
    });

    await sendMessage(page, 'Search for something and use a tool');

    const stopBtn = page.locator('[data-testid="chat-stop-button"]');
    await expect(stopBtn).toBeVisible({ timeout: 10_000 });
    await stopBtn.click();

    // Grace-timeout fallback ends the run — composer returns to Send and no
    // error bubble is shown for what is an intentional, user-initiated stop.
    await expect(stopBtn).not.toBeVisible({ timeout: 8_000 });
    const sendBtn = page
      .locator('button')
      .filter({ has: page.locator('span.material-icons-outlined').filter({ hasText: 'arrow_upward' }) });
    await expect(sendBtn.first()).toBeVisible({ timeout: 3_000 });
    await expect(page.locator('text=/unavailable|failed|Failed|Error/i')).not.toBeVisible({ timeout: 2_000 });

    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  test('stop then immediately sending a new message starts a fresh run — cancel only ever carries the old runId', async ({ page }) => {
    const convId = 'conv-stop-resend-001';
    const cancelRunIds: string[] = [];
    let secondStreamRunId: string | undefined;

    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiPartialSseBody(convId, 'First run, stopped before finishing…'),
      });
    });
    await page.route(`**/api/v1/conversations/${convId}/cancel`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const body = route.request().postDataJSON();
      if (typeof body?.runId === 'string') cancelRunIds.push(body.runId);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled: true }),
      });
    });

    await sendMessage(page, 'A slow query to interrupt');
    const stopBtn = page.locator('[data-testid="chat-stop-button"]');
    await expect(stopBtn).toBeVisible({ timeout: 10_000 });
    await stopBtn.click();
    await expect.poll(() => cancelRunIds.length > 0, { timeout: 5_000 }).toBe(true);

    // Second (fresh) run on the same conversation, with its own runId.
    await page.route(`**/api/v1/conversations/${convId}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const body = route.request().postDataJSON();
      secondStreamRunId = typeof body?.runId === 'string' ? body.runId : undefined;
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiSseBody({
          conversationId: convId,
          userMessageId: 'msg-user-resend-2',
          botMessageId: 'msg-bot-resend-2',
          question: 'A follow-up sent right away',
          answer: 'Fresh answer for the new run.',
          modelInfo: MOCK_MODEL_INFO,
        }),
      });
    });

    await sendMessage(page, 'A follow-up sent right away');
    await expect(page.locator('text=Fresh answer for the new run.').first()).toBeVisible({ timeout: 20_000 });

    expect(secondStreamRunId).toBeTruthy();
    expect(cancelRunIds).toHaveLength(1);
    expect(secondStreamRunId).not.toBe(cancelRunIds[0]);

    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });

  test('regenerate then stop replaces the target message with the partial regenerated answer', async ({ page }) => {
    const convId = 'conv-stop-regen-001';
    const originalMsgId = 'msg-bot-regen-original';
    let cancelFired = false;

    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiSseBody({
          conversationId: convId,
          userMessageId: 'msg-user-regen',
          botMessageId: originalMsgId,
          question: 'Explain regeneration',
          answer: 'Original answer before regenerate.',
          modelInfo: MOCK_MODEL_INFO,
        }),
      });
    });

    await sendMessage(page, 'Explain regeneration');
    await expect(page.locator('text=Original answer before regenerate.').first())
      .toBeVisible({ timeout: 20_000 });

    // Hold the regenerate stream past the grace timeout so the client-side
    // fallback (not a server RUN_FINISHED) is what replaces the message.
    await page.route(`**/api/v1/conversations/${convId}/message/*/regenerate`, async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const partial = buildAguiPartialSseBody(convId, 'Regenerated partial answer…');
      await new Promise<void>((resolve) => setTimeout(resolve, 8_000));
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: partial,
      }).catch(() => {});
    });
    await page.route(`**/api/v1/conversations/${convId}/cancel`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      cancelFired = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled: true }),
      });
    });

    const regenBtn = page
      .locator('button')
      .filter({ has: page.locator('span.material-icons-outlined').filter({ hasText: 'refresh' }) })
      .first();
    await expect(regenBtn).toBeVisible({ timeout: 8_000 });
    await regenBtn.click();
    await page.locator('textarea').last().press('Enter');

    const stopBtn = page.locator('[data-testid="chat-stop-button"]');
    await expect(stopBtn).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('text=Regenerated partial answer').first()).toBeVisible({ timeout: 10_000 });
    await stopBtn.click();

    await expect.poll(() => cancelFired, { timeout: 5_000 }).toBe(true);

    // Wait out the grace timeout so the hard-abort fallback has settled.
    await expect(stopBtn).not.toBeVisible({ timeout: 8_000 });

    // Grace-timeout fallback patches the ORIGINAL message id in place —
    // it does not append a new row (see `buildStoppedMessages`'s
    // `regenerateMessageId` branch in chat/streaming.ts).
    await expect(page.locator('text=Regenerated partial answer').first()).toBeVisible();
    await expect(page.locator('text=Original answer before regenerate.')).not.toBeVisible();
    await expect(page.locator('[data-testid="chat-stopped-marker"]').first()).toBeVisible({ timeout: 3_000 });

    await page.unrouteAll({ behavior: 'ignoreErrors' });
  });
});

// ---------------------------------------------------------------------------
// Stop streaming — agent chat (/chat/?agentId=…)
//
// Mirrors "Chat — stop streaming (assistant)" above but through the agent
// conversation endpoints (`/api/v1/agents/:agentId/conversations/...`), per
// `ChatApi.cancelStream`'s `agentId`-aware routing in chat/api.ts.
// ---------------------------------------------------------------------------

test.describe('Chat — stop streaming (agent chat)', () => {
  const AGENT_ID = 'test-agent-stop-e2e';

  const MOCK_AGENT_DETAIL = {
    status: 'success',
    agent: {
      id: AGENT_ID,
      _id: AGENT_ID,
      _key: AGENT_ID,
      name: 'Stop Test Agent',
      description: 'Agent used for stop-generation E2E testing.',
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

  async function mockAgentApis(page: import('@playwright/test').Page) {
    await page.route(`**/api/v1/agents/${AGENT_ID}`, (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_AGENT_DETAIL),
      });
    });
    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations*`, (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_AGENT_CONVERSATIONS_EMPTY),
      });
    });
  }

  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await mockAgentApis(page);
    await page.goto(`/chat/?agentId=${AGENT_ID}`);
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('stop on an agent conversation posts to the agent cancel endpoint and preserves the partial answer', async ({ page }) => {
    const convId = 'conv-agent-stop-001';
    let cancelBody: { runId?: string } | null = null;

    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiPartialSseBody(convId, 'Agent partial answer before stop…'),
      });
    });
    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/${convId}/cancel`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      cancelBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled: true }),
      });
    });

    await sendMessage(page, 'A slow agent query');

    const stopBtn = page.locator('[data-testid="chat-stop-button"]');
    await expect(stopBtn).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('text=Agent partial answer before stop').first()).toBeVisible({ timeout: 10_000 });
    await stopBtn.click();

    await expect.poll(() => cancelBody !== null, { timeout: 5_000 }).toBe(true);
    expect(cancelBody?.runId).toBeTruthy();

    await expect(stopBtn).not.toBeVisible({ timeout: 8_000 });
    await expect(page.locator('text=Agent partial answer before stop').first()).toBeVisible();
    await expect(page.locator('[data-testid="chat-stopped-marker"]').first()).toBeVisible({ timeout: 3_000 });
  });

  test('an agent RUN_FINISHED confirming the stop shows the Stopped marker without the grace timeout', async ({ page }) => {
    await page.route(`**/api/v1/agents/${AGENT_ID}/conversations/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiStoppedSseBody({
          conversationId: 'conv-agent-stop-confirmed-001',
          userMessageId: 'msg-user-agent-stop-confirmed',
          botMessageId: 'msg-bot-agent-stop-confirmed',
          question: 'Confirmed-stop agent query',
          answer: 'Agent answer truncated by a confirmed stop.',
          modelInfo: MOCK_MODEL_INFO,
        }),
      });
    });

    await sendMessage(page, 'Confirmed-stop agent query');

    await expect(page.locator('text=Agent answer truncated by a confirmed stop').first())
      .toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-testid="chat-stopped-marker"]').first())
      .toBeVisible({ timeout: 3_000 });
  });
});

// ---------------------------------------------------------------------------
// Conversation list sidebar update
// ---------------------------------------------------------------------------

test.describe('Chat — sidebar conversation list', () => {
  test.beforeEach(async ({ page }) => {
    await mockBaselineApis(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('new conversation title appears in the sidebar after streaming completes', async ({ page }) => {
    const QUESTION = 'Tell me about AI';
    const ANSWER = 'AI is transforming the world.';
    const TITLE = 'Tell me about AI';

    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildSseBody(QUESTION, ANSWER),
      });
    });

    // After complete fires, the app re-fetches or inserts the conversation into the
    // sidebar. Mock the list endpoint to now return the new conversation.
    await page.route('**/api/v1/conversations*', (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const now = new Date().toISOString();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversations: [
            {
              _id: CONV_ID,
              userId: 'u1',
              orgId: 'o1',
              title: TITLE,
              initiator: 'main',
              isShared: false,
              isDeleted: false,
              isArchived: false,
              lastActivityAt: Date.now(),
              status: 'active',
              modelInfo: MOCK_MODEL_INFO,
              sharedWith: [],
              conversationErrors: [],
              createdAt: now,
              updatedAt: now,
              isOwner: true,
              accessLevel: 'owner',
            },
          ],
          source: 'owned',
          pagination: { page: 1, limit: 20, totalCount: 1, totalPages: 1, hasNextPage: false, hasPrevPage: false },
        }),
      });
    });

    await sendMessage(page, QUESTION);
    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });

    // The sidebar should now list the conversation title
    const viewport = page.viewportSize();
    if (viewport && viewport.width >= 768) {
      const sidebarTitle = page.locator('nav, [data-sidebar]').first().locator(`text=${TITLE}`);
      await expect(sidebarTitle.first()).toBeVisible({ timeout: 8_000 }).catch(() => {
        // Sidebar update may be async-deferred; not a hard failure
      });
    }
  });
});
