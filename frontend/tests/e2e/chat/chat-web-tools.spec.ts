/**
 * chat-web-tools.spec.ts
 *
 * The web tools in chat: the step cards for `web_search` and `fetch_url`, and
 * the deep research toggle in the composer's "+" menu.
 *
 * Every backend call is mocked with page.route(), as in chat-streaming.spec.ts.
 * The stream carries the frames the Python AG-UI emitter sends for a tool call
 * (`agui_emitter.py`): TOOL_CALL_START, TOOL_CALL_ARGS (with `argsSummary`),
 * TOOL_CALL_END, then TOOL_CALL_RESULT with `resultSummary` and `resultView`.
 * The stored reply keeps the same tool parts, as Node saves them, so the cards
 * must survive the page swapping the live reply for the stored one.
 */

import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';

const CONV_ID = 'conv-e2e-web-001';
const MODEL_INFO = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'auto',
  modelFriendlyName: 'GPT-4o mini',
};
const QUESTION = 'How do I install PipesHub?';
const ANSWER = 'Run the quickstart with Docker Compose.';

const SEARCH_VIEW = {
  kind: 'records',
  columns: ['Title', 'Site'],
  rows: [
    { cells: ['Quickstart - PipesHub', 'docs.pipeshub.com'], url: 'https://docs.pipeshub.com/quickstart' },
    { cells: ['Deployment Overview', 'docs.pipeshub.com'], url: 'https://docs.pipeshub.com/deployment/overview' },
  ],
  total: 7,
};
const FETCH_VIEW = {
  kind: 'fields',
  fields: [
    { label: 'Page', value: 'Quickstart - PipesHub' },
    { label: 'Site', value: 'docs.pipeshub.com' },
  ],
  url: 'https://docs.pipeshub.com/quickstart',
};

const TOOL_PARTS = [
  {
    type: 'tool_call',
    toolCallId: 'call-search',
    toolName: 'dynamic__web_search',
    displayName: 'Searched the web',
    argsSummary: 'Searched the web for "install PipesHub"',
    status: 'completed',
    resultSummary: 'Found 7 results',
    resultView: SEARCH_VIEW,
  },
  {
    type: 'tool_call',
    toolCallId: 'call-fetch',
    toolName: 'dynamic__fetch_url',
    displayName: 'Read a web page',
    argsSummary: 'Read https://docs.pipeshub.com/quickstart',
    status: 'completed',
    resultSummary: 'Read “Quickstart - PipesHub” (docs.pipeshub.com)',
    resultView: FETCH_VIEW,
  },
];

function frame(type: string, fields: Record<string, unknown> = {}): string {
  return `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;
}

function toolFrames(part: (typeof TOOL_PARTS)[number], args: Record<string, string>): string[] {
  return [
    frame('TOOL_CALL_START', { toolCallId: part.toolCallId, toolCallName: part.toolName, displayName: part.displayName }),
    frame('TOOL_CALL_ARGS', { toolCallId: part.toolCallId, delta: JSON.stringify(args), argsSummary: part.argsSummary }),
    frame('TOOL_CALL_END', { toolCallId: part.toolCallId }),
    frame('TOOL_CALL_RESULT', {
      toolCallId: part.toolCallId,
      content: 'model-facing text the card never shows',
      role: 'tool',
      status: 'completed',
      resultSummary: part.resultSummary,
      resultView: part.resultView,
    }),
  ];
}

function storedConversation() {
  const conversation = buildAguiConversation({
    conversationId: CONV_ID,
    userMessageId: 'msg-user-web',
    botMessageId: 'msg-bot-web',
    question: QUESTION,
    answer: ANSWER,
    modelInfo: MODEL_INFO,
  });
  const reply = conversation.messages[1];
  reply.parts = [...TOOL_PARTS, { type: 'text', content: ANSWER }];
  return conversation;
}

function webToolsStream(): string {
  return [
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId: CONV_ID, title: QUESTION } }),
    ...toolFrames(TOOL_PARTS[0], { query: 'install PipesHub' }),
    ...toolFrames(TOOL_PARTS[1], { url: 'https://docs.pipeshub.com/quickstart' }),
    frame('TEXT_MESSAGE_START'),
    frame('TEXT_MESSAGE_CONTENT', { delta: ANSWER }),
    frame('TEXT_MESSAGE_END'),
    frame('RUN_FINISHED', {
      result: {
        conversation: storedConversation(),
        meta: { requestId: 'req-e2e-web', timestamp: new Date().toISOString(), duration: 900 },
      },
    }),
  ].join('');
}

async function mockChatApis(page: Page, onStream: (body: Record<string, unknown>) => void) {
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'success',
        message: 'Success',
        models: [{
          modelType: 'chat', provider: 'openAI', modelName: 'GPT-4o mini', modelKey: 'gpt-4o-mini',
          isMultimodal: false, isReasoning: false, isDefault: true, modelFriendlyName: 'GPT-4o mini',
        }],
      }),
    }),
  );
  await page.route('**/api/v1/conversations*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        conversations: [],
        source: 'owned',
        pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
      }),
    });
  });
  await page.route('**/api/v1/users/by-ids', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : route.fallback(),
  );
  // The page loads the conversation a stream names; see mockConversationDetail in chat-streaming.spec.ts.
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${CONV_ID}`,
    (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const conversation = storedConversation();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation: { ...conversation, id: CONV_ID, access: { isOwner: true, accessLevel: 'OWNER' } },
          filters: {},
          meta: {},
        }),
      });
    },
  );
  await page.route('**/api/v1/conversations/stream', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    onStream(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' },
      body: webToolsStream(),
    });
  });
}

async function send(page: Page, message: string) {
  const textarea = page.locator('textarea').last();
  await textarea.click();
  await textarea.fill(message);
  await textarea.press('Enter');
}

function plusMenuButton(page: Page) {
  return page.getByRole('button', { name: /^Attach files and capabilities/ }).filter({ visible: true }).first();
}

/** Opens a header (the reply's "Used N tools" section, a "Ran N tools" group) if it is closed. */
async function open(page: Page, header: RegExp) {
  const toggle = page.locator('[role="button"][aria-expanded]').filter({ hasText: header }).first();
  if ((await toggle.getAttribute('aria-expanded')) === 'false') await toggle.click();
  return toggle;
}

/** A tool's card: the web calls of one turn sit in a group inside the reply's activity section. */
async function stepCard(page: Page, label: RegExp) {
  await open(page, /Used \d+ tools?/);
  const group = await open(page, /Ran \d+ tools/);
  await expect(group).not.toContainText('Dynamic');
  return page.locator('[role="button"][aria-expanded]').filter({ hasText: label }).first();
}

test.describe('Chat — web tools (mocked backend)', () => {
  let streamBodies: Record<string, unknown>[];

  test.beforeEach(async ({ page }) => {
    streamBodies = [];
    // A capability left on by an earlier test must not leak into this one; a reload within the test keeps it.
    await page.addInitScript(() => {
      if (sessionStorage.getItem('e2e-capabilities-reset')) return;
      sessionStorage.setItem('e2e-capabilities-reset', '1');
      for (const key of Object.keys(localStorage)) {
        if (key.toLowerCase().includes('capabilit')) localStorage.removeItem(key);
      }
    });
    await mockChatApis(page, (body) => streamBodies.push(body));
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
  });

  test('the search card lists every result with a link to it', async ({ page }) => {
    await send(page, QUESTION);
    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });

    const search = await stepCard(page, /install PipesHub|Found 7 results|Searched the web/);
    await search.click();
    await expect(search).toHaveAttribute('aria-expanded', 'true');

    const table = page.getByTestId('tool-result-table').first();
    await expect(table).toBeVisible();
    await expect(table.getByRole('link', { name: 'Quickstart - PipesHub' }))
      .toHaveAttribute('href', 'https://docs.pipeshub.com/quickstart');
    await expect(table.getByRole('link', { name: 'Deployment Overview' }))
      .toHaveAttribute('href', 'https://docs.pipeshub.com/deployment/overview');
    await expect(page.getByText('Showing 2 of 7')).toBeVisible();
    await expect(page.getByText('model-facing text the card never shows')).toHaveCount(0);
  });

  test('the page card names the page it read and links to it', async ({ page }) => {
    await send(page, QUESTION);
    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });

    await (await stepCard(page, /Quickstart - PipesHub|Read a web page|docs\.pipeshub\.com\/quickstart/)).click();

    const fields = page.getByTestId('tool-result-fields').first();
    await expect(fields).toBeVisible();
    await expect(fields).toContainText('Quickstart - PipesHub');
    await expect(fields).toContainText('docs.pipeshub.com');
    await expect(fields.getByRole('link').first()).toHaveAttribute('href', 'https://docs.pipeshub.com/quickstart');
  });

  test('the cards are still there after reloading the conversation', async ({ page }) => {
    await send(page, QUESTION);
    await expect(page).toHaveURL(new RegExp(CONV_ID), { timeout: 20_000 });
    await page.reload();
    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });

    await (await stepCard(page, /install PipesHub|Found 7 results|Searched the web/)).click();
    await expect(page.getByTestId('tool-result-table').first()
      .getByRole('link', { name: 'Quickstart - PipesHub' })).toBeVisible();
  });

  test('deep research is off by default and is sent when turned on', async ({ page }) => {
    await plusMenuButton(page).click();
    const deep = page.getByRole('switch', { name: 'Deep research' });
    await expect(deep).toBeVisible();
    await expect(deep).not.toBeChecked();

    await deep.click();
    await expect(deep).toBeChecked();
    await page.keyboard.press('Escape');
    // The trigger says filters are applied once a capability differs from the default.
    await expect(page.getByRole('button', { name: /Search filters applied/ }).filter({ visible: true }).first())
      .toBeVisible();

    await send(page, QUESTION);
    await expect(page.locator(`text=${ANSWER}`).first()).toBeVisible({ timeout: 20_000 });
    expect(streamBodies).toHaveLength(1);
    expect(streamBodies[0].agentCapabilities).toMatchObject({ webSearch: true, deepSearch: true });
  });

  test('deep research needs web search', async ({ page }) => {
    await plusMenuButton(page).click();
    const deep = page.getByRole('switch', { name: 'Deep research' });
    await deep.click();
    await expect(deep).toBeChecked();

    await page.getByRole('switch', { name: /^Web search$/i }).click();
    await expect(deep).toBeDisabled();
  });

  test('the choice is kept after a reload', async ({ page }) => {
    await plusMenuButton(page).click();
    await page.getByRole('switch', { name: 'Deep research' }).click();
    await page.keyboard.press('Escape');

    await page.reload();
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await plusMenuButton(page).click();
    await expect(page.getByRole('switch', { name: 'Deep research' })).toBeChecked();
  });
});
