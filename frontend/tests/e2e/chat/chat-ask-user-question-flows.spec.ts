/**
 * Journey-level ask_user_question coverage: reload, happy path, skip, stop,
 * regenerate, many questions, extra tools, chat-then-answer, and one open card.
 * Card-widget clicks live in chat-ask-user-question.spec.ts.
 */
import { test, expect } from '../fixtures/base.fixture';
import {
  buildAguiAskUserQuestionSseBody,
  buildAguiConversation,
  buildAguiSseBody,
  buildAguiPartialSseBody,
} from './agui-sse-builder';
import { serveOpenSseStream } from './open-sse-stream';

const MODEL = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'auto',
  modelFriendlyName: 'GPT-4o mini',
};

const LLMS = {
  status: 'success',
  models: [{
    modelType: 'chat',
    provider: 'openAI',
    modelName: 'GPT-4o mini',
    modelKey: 'gpt-4o-mini',
    isMultimodal: false,
    isReasoning: false,
    isDefault: true,
    modelFriendlyName: 'GPT-4o mini',
  }],
  message: 'Success',
};

const EMPTY_LIST = {
  conversations: [],
  source: 'owned',
  pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
};

type AskQ = {
  uuid: string;
  question: string;
  options: Array<{ id: string; label: string; isUserInput: boolean }>;
  multiSelect: boolean;
};

const q = (uuid: string, question: string, labels: string[], multiSelect = false): AskQ => ({
  uuid,
  question,
  multiSelect,
  options: labels.map((label, i) => ({ id: `${uuid}-o${i}`, label, isUserInput: false })),
});

const REGION = q('q-region', 'Which region?', ['EU', 'US']);
const FORMAT = q('q-format', 'Which format?', ['Table', 'Chart']);
const DEPT = q('q-dept', 'Which department are you in?', ['Engineering', 'Marketing', 'Sales']);

function now() {
  return new Date().toISOString();
}

function msg(overrides: Record<string, unknown>) {
  const t = now();
  return {
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    feedback: [],
    createdAt: t,
    updatedAt: t,
    ...overrides,
  };
}

function askSse(conversationId: string, query: string, questions: AskQ[], userIntent?: string) {
  return buildAguiAskUserQuestionSseBody({
    conversationId,
    userMessageId: `${conversationId}-user`,
    botMessageId: `${conversationId}-bot`,
    question: query,
    modelInfo: MODEL,
    toolData: { name: 'ask_user_question', ...(userIntent ? { userIntent } : {}), questions },
  });
}

function answerSse(conversationId: string, query: string, answer: string) {
  return buildAguiSseBody({
    conversationId,
    userMessageId: `${conversationId}-sel`,
    botMessageId: `${conversationId}-reply`,
    question: query,
    answer,
    modelInfo: MODEL,
  });
}

function sseWithMessages(
  conversationId: string,
  answer: string,
  messages: Record<string, unknown>[],
) {
  const conversation = storedConversation(conversationId, messages);
  const frame = (type: string, fields: Record<string, unknown> = {}) =>
    `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;
  return [
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId } }),
    frame('TEXT_MESSAGE_START'),
    frame('TEXT_MESSAGE_CONTENT', { delta: answer }),
    frame('TEXT_MESSAGE_END'),
    frame('RUN_FINISHED', { result: { conversation } }),
  ].join('');
}

function storedConversation(
  conversationId: string,
  messages: Record<string, unknown>[],
  pagination?: Record<string, unknown>,
) {
  const t = now();
  return {
    _id: conversationId,
    userId: 'user-e2e',
    orgId: 'org-e2e',
    title: 'Ask flow',
    initiator: 'main',
    messages,
    isShared: false,
    isDeleted: false,
    isArchived: false,
    lastActivityAt: Date.now(),
    status: 'active',
    modelInfo: MODEL,
    sharedWith: [],
    conversationErrors: [],
    createdAt: t,
    updatedAt: t,
    __v: 0,
    pagination: pagination ?? {
      page: 1,
      limit: 20,
      totalCount: messages.length,
      totalPages: 1,
      hasNextPage: false,
      hasPrevPage: false,
    },
  };
}

async function mockBaseline(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/org/onboarding-status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'configured' }),
    }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(LLMS) }),
  );
  await page.route('**/api/v1/conversations*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(EMPTY_LIST),
      });
    }
    return route.continue();
  });
  // Real backend 400s a made-up user id; that toast covers Send and Stop.
  await page.route('**/api/v1/users/by-ids', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });
}

async function mockConversationGet(
  page: import('@playwright/test').Page,
  stored: { _id: string; messages: unknown[] } | (() => { _id: string; messages: unknown[] }),
) {
  const current = typeof stored === 'function' ? stored : () => stored;
  const path = `/api/v1/conversations/${current()._id}`;
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === path,
    (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const conversation = current();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation: {
            ...conversation,
            id: conversation._id,
            access: { isOwner: true, accessLevel: 'OWNER' },
          },
          filters: {},
          meta: {},
        }),
      });
    },
  );
}

async function send(page: import('@playwright/test').Page, text: string) {
  const textarea = page.locator('textarea').last();
  await textarea.click();
  await textarea.fill(text);
  await textarea.press('Enter');
}

function submitBtn(page: import('@playwright/test').Page) {
  return page.locator('button').filter({ hasText: /^Submit$/ });
}

function skipBtn(page: import('@playwright/test').Page) {
  return page.locator('button').filter({ hasText: /^Skip$/ });
}

// A fully answered card opens closed (Show more reveals it); builds before that show the question at once.
async function openClosedCards(page: import('@playwright/test').Page, question: string) {
  const more = page.getByRole('button', { name: /^Show more/ });
  await expect(more.or(page.getByText(question)).first()).toBeVisible({ timeout: 20_000 });
  while (await more.count()) await more.first().click();
}

test.describe('Ask user question — journeys', () => {
  test('happy path: one question, submit, reply on the same turn, card closes', async ({ page }) => {
    const id = 'conv-ask-happy';
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, []));
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: askSse(id, route.request().postDataJSON().query, [DEPT]),
      });
    });
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await send(page, 'Help me write a report');
    await expect(page.getByText('Which department are you in?')).toBeVisible({ timeout: 20_000 });
    await expect(submitBtn(page)).toHaveCount(1);

    await page.route(`**/api/v1/conversations/${id}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: answerSse(id, route.request().postDataJSON().query, 'Here is the Engineering report.'),
      });
    });
    await page.locator('[role="radio"]').first().click();
    await submitBtn(page).first().click();
    await expect(page.getByText('Here is the Engineering report.')).toBeVisible({ timeout: 20_000 });
    await expect(submitBtn(page)).toHaveCount(0);
  });

  test('skip sends no preference and closes the card', async ({ page }) => {
    const id = 'conv-ask-skip';
    let body = '';
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, []));
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: askSse(id, route.request().postDataJSON().query, [REGION]),
      });
    });
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await send(page, 'plan the launch');
    await expect(page.getByText('Which region?')).toBeVisible({ timeout: 20_000 });

    await page.route(`**/api/v1/conversations/${id}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      body = route.request().postData() ?? '';
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: answerSse(id, 'User selections:', 'Going global.'),
      });
    });
    await skipBtn(page).first().click();
    await expect(page.getByText('Going global.')).toBeVisible({ timeout: 20_000 });
    expect(body).toMatch(/No preference/i);
    await expect(submitBtn(page)).toHaveCount(0);
  });

  test('reload of an answered card shows every answer and no Submit', async ({ page }) => {
    const id = 'conv-ask-reload';
    const t = now();
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, [
      msg({ _id: 'u1', messageType: 'user_query', content: 'plan the launch', createdAt: t, updatedAt: t }),
      msg({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions: [REGION, FORMAT] } }],
      }),
      msg({ _id: 'b1', messageType: 'bot_response', content: '' }),
      msg({
        _id: 's1',
        messageType: 'user_query',
        content: 'User selections:\n1. "Which region?" → EU\n2. "Which format?" → Chart',
      }),
      msg({ _id: 'b2', messageType: 'bot_response', content: 'Planning the EU chart.' }),
    ]));
    await page.goto(`/chat/?conversationId=${id}`);
    await openClosedCards(page, 'Which region?');
    await expect(page.getByText('Which region?')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('Which format?')).toBeVisible();
    await expect(page.getByText('Planning the EU chart.')).toBeVisible();
    await expect(page.getByText('EU').first()).toBeVisible();
    await expect(page.getByText('Chart').first()).toBeVisible();
    await expect(submitBtn(page)).toHaveCount(0);
    await expect(page.getByText('User selections:')).toHaveCount(0);
  });

  test('reload of 17 answered questions keeps them on one closed card', async ({ page }) => {
    const id = 'conv-ask-17';
    const questions = Array.from({ length: 17 }, (_, i) =>
      q(`q${i + 1}`, `Random question number ${i + 1}?`, ['Yes', 'No']),
    );
    const lines = questions.map((item, i) => `${i + 1}. "${item.question}" → Yes`);
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, [
      msg({ _id: 'u0', messageType: 'user_query', content: 'ask me random questions' }),
      msg({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions } }],
      }),
      msg({ _id: 'b1', messageType: 'bot_response', content: '' }),
      msg({ _id: 's1', messageType: 'user_query', content: `User selections:\n${lines.join('\n')}` }),
      msg({ _id: 'b2', messageType: 'bot_response', content: 'That was fun.' }),
    ]));
    await page.goto(`/chat/?conversationId=${id}`);
    await openClosedCards(page, 'Random question number 1?');
    await expect(page.getByText('Random question number 1?')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('Random question number 17?')).toBeVisible();
    await expect(page.getByText('That was fun.')).toBeVisible();
    await expect(submitBtn(page)).toHaveCount(0);
  });

  test('search and slack tools in the same turn still leave one card', async ({ page }) => {
    const id = 'conv-ask-tools';
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, [
      msg({ _id: 'u1', messageType: 'user_query', content: 'search then ask' }),
      msg({ _id: 'ts', messageType: 'tool_call', tools: [{ toolName: 'search', toolResult: { hits: 3 } }] }),
      msg({ _id: 'tk', messageType: 'tool_call', tools: [{ toolName: 'slack', toolResult: { ok: true } }] }),
      msg({
        _id: 'ta',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions: [REGION] } }],
      }),
      msg({ _id: 'b1', messageType: 'bot_response', content: '' }),
    ]));
    await page.goto(`/chat/?conversationId=${id}`);
    await expect(page.getByText('Which region?')).toBeVisible({ timeout: 20_000 });
    await expect(submitBtn(page)).toHaveCount(1);
    await expect(page.locator('[role="radio"]')).toHaveCount(3);
  });

  test('only the newest unanswered card is interactive', async ({ page }) => {
    const id = 'conv-ask-one-open';
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, [
      msg({ _id: 'u1', messageType: 'user_query', content: 'plan the launch' }),
      msg({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions: [REGION] } }],
      }),
      msg({ _id: 'b1', messageType: 'bot_response', content: '' }),
      msg({ _id: 's1', messageType: 'user_query', content: 'User selections:\n1. "Which region?" → EU' }),
      msg({ _id: 'b2', messageType: 'bot_response', content: 'Planning for EU.' }),
      msg({ _id: 'u2', messageType: 'user_query', content: 'now the report' }),
      msg({
        _id: 't2',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions: [FORMAT] } }],
      }),
      msg({ _id: 'b3', messageType: 'bot_response', content: '' }),
    ]));
    await page.goto(`/chat/?conversationId=${id}`);
    await expect(page.getByText('Which format?')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('Planning for EU.')).toBeVisible();
    await expect(submitBtn(page)).toHaveCount(1);
    await openClosedCards(page, 'Which region?');
    await expect(page.getByText('Which region?')).toBeVisible();
  });

  test('a later hi answers the open card: the card closes and the reply shows', async ({ page }) => {
    const id = 'conv-ask-then-hi';
    const asked = [
      msg({ _id: 'u1', messageType: 'user_query', content: 'helpme' }),
      msg({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions: [REGION] } }],
      }),
      msg({ _id: 'b1', messageType: 'bot_response', content: '' }),
    ];
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, []));
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: askSse(id, route.request().postDataJSON().query, [REGION]),
      });
    });
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await send(page, 'helpme');
    await expect(page.getByText('Which region?')).toBeVisible({ timeout: 20_000 });

    await page.route(`**/api/v1/conversations/${id}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const query = String(route.request().postDataJSON().query ?? '');
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: sseWithMessages(id, 'Hi! How can I help you today?', [
          ...asked,
          msg({ _id: 'u2', messageType: 'user_query', content: query }),
          msg({ _id: 'b2', messageType: 'bot_response', content: 'Hi! How can I help you today?' }),
        ]),
      });
    });
    await send(page, 'hiii');
    await expect(page.getByText('Hi! How can I help you today?')).toBeVisible({ timeout: 20_000 });
    // Typing while the card is open answers it, so the card closes.
    await expect(submitBtn(page)).toHaveCount(0);
    await expect(page.getByTestId('chat-stopped-marker')).toHaveCount(0);
  });

  test('a later chat answers the open card once and does not copy onto it', async ({ page }) => {
    const id = 'conv-ask-then-later-list';
    const later = 'You asked me to: send an email saying Hello from Pipeshub.';
    const asked = [
      msg({ _id: 'u1', messageType: 'user_query', content: 'ask me one question' }),
      msg({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions: [REGION] } }],
      }),
      msg({ _id: 'b1', messageType: 'bot_response', content: '' }),
    ];
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, []));
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: askSse(id, route.request().postDataJSON().query, [REGION]),
      });
    });
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await send(page, 'ask me one question');
    await expect(page.getByText('Which region?')).toBeVisible({ timeout: 20_000 });

    await page.route(`**/api/v1/conversations/${id}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const query = String(route.request().postDataJSON().query ?? '');
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: sseWithMessages(id, later, [
          ...asked,
          msg({ _id: 'u2', messageType: 'user_query', content: query }),
          msg({ _id: 'b2', messageType: 'bot_response', content: later }),
        ]),
      });
    });
    await send(page, 'what are the things i asked u to do');
    await expect(page.getByText(later)).toBeVisible({ timeout: 20_000 });
    // Typing while the card is open answers it, so the card closes.
    await expect(submitBtn(page)).toHaveCount(0);
    await expect(page.getByText(later)).toHaveCount(1);
  });

  test('five questions in one card: Next through them, then Submit once', async ({ page }) => {
    const id = 'conv-ask-five';
    const questions = Array.from({ length: 5 }, (_, i) =>
      q(`q${i + 1}`, `Pick option for question ${i + 1}?`, ['Alpha', 'Beta']),
    );
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, []));
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: askSse(id, route.request().postDataJSON().query, questions),
      });
    });
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await send(page, 'ask me five questions');
    await expect(page.getByText('Pick option for question 1?')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/Step 1 of 5/i)).toBeVisible();

    for (let step = 1; step <= 5; step += 1) {
      await page.locator('[role="radio"]').first().click();
      if (step < 5) {
        await page.locator('button').filter({ hasText: /^Next$/ }).first().click();
        await expect(page.getByText(`Pick option for question ${step + 1}?`)).toBeVisible();
      }
    }

    let sent = '';
    await page.route(`**/api/v1/conversations/${id}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      sent = route.request().postData() ?? '';
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: answerSse(id, 'User selections:', 'All five noted.'),
      });
    });
    await submitBtn(page).first().click();
    await expect(page.getByText('All five noted.')).toBeVisible({ timeout: 20_000 });
    expect(sent).toContain('question 1');
    expect(sent).toContain('question 5');
    await expect(submitBtn(page)).toHaveCount(0);
  });

  test('Stop during a resume keeps the card and does not park the greeting on Stopped', async ({ page }) => {
    const id = 'conv-ask-stop-resume';
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, []));
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: askSse(id, route.request().postDataJSON().query, [REGION]),
      });
    });
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await send(page, 'plan the launch');
    await expect(page.getByText('Which region?')).toBeVisible({ timeout: 20_000 });

    let cancelBody: { runId?: string } | null = null;
    await page.route(`**/api/v1/conversations/${id}/cancel`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      cancelBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled: true }),
      });
    });
    await serveOpenSseStream(
      page,
      `/api/v1/conversations/${id}/messages/stream`,
      buildAguiPartialSseBody(id, 'Working on EU…'),
    );
    await page.locator('[role="radio"]').first().click();
    await submitBtn(page).first().click();
    await expect(page.getByText('Working on EU')).toBeVisible({ timeout: 10_000 });
    const stop = page.getByTestId('chat-stop-button');
    await expect(stop).toBeVisible({ timeout: 5_000 });
    await stop.click();
    await expect.poll(() => cancelBody !== null, { timeout: 5_000 }).toBe(true);
    await expect(stop).not.toBeVisible({ timeout: 8_000 });
    await openClosedCards(page, 'Which region?');
    await expect(page.getByText('Which region?')).toBeVisible();
  });

  test('regenerate after an answered card keeps the saved answers and shows the new reply', async ({ page }) => {
    const id = 'conv-ask-regen';
    const messages = [
      msg({ _id: 'u1', messageType: 'user_query', content: 'plan the launch' }),
      msg({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions: [REGION] } }],
      }),
      msg({ _id: 'b1', messageType: 'bot_response', content: '' }),
      msg({ _id: 's1', messageType: 'user_query', content: 'User selections:\n1. "Which region?" → EU' }),
      msg({ _id: 'b2', messageType: 'bot_response', content: 'Planning for EU.' }),
    ];
    await mockBaseline(page);
    let stored = messages;
    await mockConversationGet(page, () => storedConversation(id, stored));
    await page.goto(`/chat/?conversationId=${id}`);
    await expect(page.getByText('Planning for EU.')).toBeVisible({ timeout: 20_000 });

    await page.route(new RegExp(`/api/v1/conversations/${id}/message/.*/regenerate`), (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      stored = [
        messages[0],
        messages[1],
        messages[2],
        messages[3],
        msg({ _id: 'b2', messageType: 'bot_response', content: 'Regenerated plan for EU.' }),
      ];
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: buildAguiSseBody({
          conversationId: id,
          userMessageId: 'u1',
          botMessageId: 'b2',
          question: 'plan the launch',
          answer: 'Regenerated plan for EU.',
          modelInfo: MODEL,
        }),
      });
    });
    const regen = page
      .locator('button')
      .filter({ has: page.locator('span.material-icons-outlined').filter({ hasText: 'refresh' }) })
      .first();
    await expect(regen).toBeVisible({ timeout: 8_000 });
    await regen.click();
    await expect(page.getByText('Regenerate response')).toBeVisible({ timeout: 5_000 });
    const sendRegen = page.getByRole('button', { name: 'Send message' });
    await expect(sendRegen).toBeVisible({ timeout: 5_000 });
    await sendRegen.click();
    await expect(page.getByText('Regenerated plan for EU.')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('Planning for EU.')).toHaveCount(0);
    await openClosedCards(page, 'Which region?');
    await expect(page.getByText('Which region?')).toBeVisible();
    await expect(page.getByText('EU', { exact: true })).toBeVisible();
  });

  test('a comma in the picked label is sent whole', async ({ page }) => {
    const id = 'conv-ask-comma';
    const metric = q('q-metric', 'Which metric?', ['Revenue, net of tax', 'Headcount']);
    let body = '';
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, []));
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: askSse(id, route.request().postDataJSON().query, [metric]),
      });
    });
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await send(page, 'compare revenue');
    await expect(page.getByText('Which metric?')).toBeVisible({ timeout: 20_000 });
    await page.route(`**/api/v1/conversations/${id}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      body = route.request().postData() ?? '';
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: answerSse(id, 'User selections:', 'Comparing revenue.'),
      });
    });
    await page.getByText('Revenue, net of tax').click();
    await submitBtn(page).first().click();
    await expect(page.getByText('Comparing revenue.')).toBeVisible({ timeout: 20_000 });
    expect(body).toContain('Revenue, net of tax');
  });
});

test.describe('Ask user question — stop a first turn that only asked', () => {
  test('Stop as the card appears, then chat: the chat answers the card and nothing is left Stopped', async ({ page }) => {
    const id = 'conv-ask-stop-then-chat';
    const asked = [
      msg({ _id: 'u1', messageType: 'user_query', content: 'helpme' }),
      msg({
        _id: 't1',
        messageType: 'tool_call',
        tools: [{ toolName: 'ask_user_question', toolResult: { name: 'ask_user_question', questions: [REGION] } }],
      }),
      msg({ _id: 'b1', messageType: 'bot_response', content: '', status: 'stopped' }),
    ];
    await mockBaseline(page);
    await mockConversationGet(page, storedConversation(id, []));
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });

    let cancelBody: { runId?: string } | null = null;
    await page.route(`**/api/v1/conversations/${id}/cancel`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      cancelBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled: true }),
      });
    });
    const openAsk = askSse(id, 'helpme', [REGION]).replace(/event: RUN_FINISHED[\s\S]*$/, '');
    await serveOpenSseStream(page, '/api/v1/conversations/stream', openAsk);

    await send(page, 'helpme');
    await expect(page.getByText('Which region?')).toBeVisible({ timeout: 20_000 });
    const stop = page.getByTestId('chat-stop-button');
    await expect(stop).toBeVisible({ timeout: 5_000 });
    await stop.click();
    await expect.poll(() => cancelBody !== null, { timeout: 5_000 }).toBe(true);
    await expect(stop).not.toBeVisible({ timeout: 8_000 });
    await expect(submitBtn(page)).toHaveCount(1);

    await page.route(`**/api/v1/conversations/${id}/messages/stream`, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const query = String(route.request().postDataJSON().query ?? '');
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: sseWithMessages(id, 'Hi! How can I help you today?', [
          ...asked,
          msg({ _id: 'u2', messageType: 'user_query', content: query }),
          msg({ _id: 'b2', messageType: 'bot_response', content: 'Hi! How can I help you today?' }),
        ]),
      });
    });
    await send(page, 'hiii');
    await expect(page.getByText('Hi! How can I help you today?')).toBeVisible({ timeout: 20_000 });
    // Typing while the card is open answers it, so the card closes.
    await expect(submitBtn(page)).toHaveCount(0);
    await expect(page.getByTestId('chat-stopped-marker')).toHaveCount(0);
  });

  test('Stop while the card is on screen leaves the card interactive', async ({ page }) => {
    const id = 'conv-ask-stop-first';
    await mockBaseline(page);
    await mockConversationGet(
      page,
      buildAguiConversation({
        conversationId: id,
        userMessageId: `${id}-u`,
        question: 'ask me something',
        modelInfo: MODEL,
      }),
    );
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await page.route('**/api/v1/conversations/stream', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: askSse(id, route.request().postDataJSON().query, [REGION]),
      });
    });
    await send(page, 'ask me something');
    await expect(page.getByText('Which region?')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('chat-stop-button')).toHaveCount(0);
    await expect(submitBtn(page)).toHaveCount(1);
  });
});
