/**
 * chat-scroll.spec.ts
 *
 * A safety net for how the chat message list scrolls today: where an opened
 * conversation lands, where a sent question lands, following a streaming
 * answer, stopping that with the wheel, loading older messages, and switching
 * conversations. Positions are read from the real layout (bounding boxes and
 * scrollTop), so a change that moves any of them fails here. Where today's
 * behaviour is not the intended one, the test says so and asserts what the
 * code does, so a fix has to update it on purpose.
 *
 * Every request is answered by page.route, the app shell's included, and the
 * session is a made-up token, so the spec needs no backend, sign-in or model:
 *   npx playwright test --project=authenticated --no-deps tests/e2e/chat/chat-scroll.spec.ts
 */

import type { Page, Route } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation, buildAguiPartialSseBody } from './agui-sse-builder';

const MODEL = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'internal_search',
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

const b64 = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
// The app reads the user id and expiry from the token; nothing checks its signature here.
const TOKEN = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ userId: 'user-e2e', orgId: 'org-e2e', exp: 4102444800 })}.e2e`;

// The session comes from TOKEN, so the spec does not need the saved sign-in.
test.use({ storageState: { cookies: [], origins: [] } });

type Message = Record<string, unknown>;

const SCROLLER = '.chat-message-scroll';

const question = (chat: string, n: number) => `${chat} question ${n}?`;

/** The stored messages of turns `from`..`to`, oldest first; each answer is three paragraphs. */
function turns(chat: string, from: number, to: number): Message[] {
  const messages: Message[] = [];
  for (let n = from; n <= to; n++) {
    messages.push(...buildAguiConversation({
      conversationId: chat,
      userMessageId: `${chat}-q${n}`,
      botMessageId: `${chat}-a${n}`,
      question: question(chat, n),
      answer: [1, 2, 3]
        .map((part) => `${chat} answer ${n}, part ${part}. The team checked each figure against its source and noted what changed.`)
        .join('\n\n'),
      modelInfo: MODEL,
    }).messages);
  }
  return messages;
}

const streamedLine = (chat: string, i: number) =>
  `${chat} streamed line ${i}. Each line adds another paragraph while the answer is still being written.`;

/** A conversation as the API returns it, titled with its id. */
function conversation(chat: string, messages: Message[]) {
  return { ...buildAguiConversation({ conversationId: chat, userMessageId: `${chat}-title`, question: chat, modelInfo: MODEL }), messages };
}

const frame = (type: string, fields: Record<string, unknown> = {}) =>
  `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;

/** The end of a run: RUN_FINISHED carries the whole stored conversation, as the backend sends it. */
function finishFrames(chat: string, messages: Message[]): string {
  return frame('TEXT_MESSAGE_END') + frame('RUN_FINISHED', {
    result: {
      conversation: conversation(chat, messages),
      meta: { requestId: 'req-e2e-scroll', timestamp: new Date().toISOString(), duration: 1 },
    },
  });
}

function json(route: Route, body: unknown) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

/**
 * Answers the app shell and the conversation routes from `chats` (id → stored
 * messages, oldest first). Message pages are cut from the newest end, as the
 * API pages them: page 1 holds the latest `limit` messages.
 */
async function mockApp(page: Page, chats: Map<string, Message[]>) {
  await page.addInitScript((token) => {
    localStorage.setItem('jwt_access_token', token);
    localStorage.setItem('jwt_refresh_token', token);
    // Builds that keep a sign-in per organization read the tokens from this map instead.
    localStorage.setItem('pipeshub_org_tokens', JSON.stringify({ 'org-e2e': { accessToken: token, refreshToken: token } }));
  }, TOKEN);
  // Registered first, so every route below wins; anything else the shell asks for gets an empty body.
  await page.route('**/api/**', (route) => json(route, {}));
  await page.route('**/api/v1/health{,/services}', (route) => json(route, { status: 'healthy', services: {} }));
  await page.route('**/api/v1/users/user-e2e', (route) =>
    json(route, { fullName: 'E2E User', email: 'e2e@example.com', hasLoggedIn: true }));
  await page.route('**/api/v1/userGroups/users/*', (route) => json(route, []));
  // Builds that gate actions on group permissions read the user's resolved set here.
  await page.route('**/api/v1/userGroups/users/*/permissions', (route) => json(route, { permissions: {} }));
  await page.route('**/api/v1/org/onboarding-status', (route) => json(route, { status: 'configured' }));
  await page.route(/\/api\/v1\/(users\/dp|org\/logo)$/, (route) => route.fulfill({ status: 204 }));
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) => json(route, LLMS));
  await page.route((url) => url.pathname.replace(/\/$/, '') === '/api/v1/conversations', (route) => {
    const owned = new URL(route.request().url()).searchParams.get('source') !== 'shared';
    const conversations = owned ? [...chats.keys()].map((chat) => ({ ...conversation(chat, []), isOwner: true })) : [];
    return json(route, {
      conversations,
      source: owned ? 'owned' : 'shared',
      pagination: { page: 1, limit: 10, totalCount: conversations.length, totalPages: 1, hasNextPage: false, hasPrevPage: false },
    });
  });
  await page.route((url) => /^\/api\/v1\/conversations\/[^/]+\/?$/.test(url.pathname), (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const url = new URL(route.request().url());
    const chat = url.pathname.split('/')[4];
    const all = chats.get(chat) ?? [];
    const pageNo = Number(url.searchParams.get('page') ?? 1);
    const limit = Number(url.searchParams.get('limit') ?? 20);
    const end = all.length - (pageNo - 1) * limit;
    return json(route, {
      conversation: {
        ...conversation(chat, all.slice(Math.max(0, end - limit), Math.max(0, end))),
        id: chat,
        access: { isOwner: true, accessLevel: 'OWNER' },
        pagination: {
          page: pageNo,
          limit,
          totalCount: all.length,
          totalPages: Math.ceil(all.length / limit),
          hasNextPage: end - limit > 0,
          hasPrevPage: pageNo > 1,
        },
      },
      filters: {},
      meta: {},
    });
  });
}

/**
 * Answers the next POST to `path` with an SSE response that starts with `body`
 * and stays open; `push` adds frames to it later. `serveOpenSseStream`
 * (open-sse-stream.ts) sends its whole body at once, and a growing answer needs
 * more frames after the test has looked. Call after navigating: it wraps
 * `window.fetch` in the page.
 */
async function holdSseStream(page: Page, path: string, body: string) {
  await page.evaluate(({ path, body }) => {
    const realFetch = window.fetch.bind(window);
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : null;
      const url = new URL(request ? request.url : String(input), window.location.href);
      const method = (init?.method ?? request?.method ?? 'GET').toUpperCase();
      if (method !== 'POST' || url.pathname !== path) return realFetch(input, init);
      window.fetch = realFetch;
      const encoder = new TextEncoder();
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          (window as unknown as { __e2ePush: (chunk: string) => void }).__e2ePush = (chunk) =>
            controller.enqueue(encoder.encode(chunk));
          controller.enqueue(encoder.encode(body));
        },
      });
      return Promise.resolve(new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
    };
  }, { path, body });
}

async function push(page: Page, chunk: string) {
  await page.evaluate((c) => (window as unknown as { __e2ePush: (chunk: string) => void }).__e2ePush(c), chunk);
}

/** Streams paragraphs `from`..`to` into the held answer and waits until the last one is on the page. */
async function grow(page: Page, chat: string, from: number, to: number) {
  for (let i = from; i <= to; i++) {
    await push(page, frame('TEXT_MESSAGE_CONTENT', { delta: `\n\n${streamedLine(chat, i)}` }));
  }
  await expect(page.getByText(streamedLine(chat, to))).toBeAttached();
}

async function send(page: Page, text: string) {
  const textarea = page.locator('textarea').last();
  await textarea.click();
  await textarea.fill(text);
  await textarea.press('Enter');
}

/** The run is over once Stop has gone again. */
async function runFinished(page: Page) {
  await expect(page.locator('[data-testid="chat-stop-button"]')).toHaveCount(0);
}

/** Wheel over the middle of the message list, as a reader scrolling it. */
async function wheel(page: Page, deltaY: number) {
  const box = await page.locator(SCROLLER).boundingBox();
  if (!box) throw new Error('message list not on the page');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, deltaY);
}

/**
 * The list's scroll state, and how far below the list's top edge the heading
 * of the question containing `text` sits (NaN while it is not rendered).
 */
async function measure(page: Page, text = '') {
  return page.evaluate(({ selector, text }) => {
    const list = document.querySelector<HTMLElement>(selector);
    if (!list) return { top: NaN, fromBottom: NaN, height: NaN, offset: NaN };
    const heading = text
      ? [...document.querySelectorAll('[data-testid="user-query-heading"]')].find((h) => h.textContent?.includes(text))
      : undefined;
    return {
      top: list.scrollTop,
      fromBottom: list.scrollHeight - list.scrollTop - list.clientHeight,
      height: list.clientHeight,
      offset: heading ? heading.getBoundingClientRect().top - list.getBoundingClientRect().top : NaN,
    };
  }, { selector: SCROLLER, text });
}

/** The first question heading at least partly inside the list: its question text and offset from the list's top edge. */
async function firstVisibleQuestion(page: Page) {
  return page.evaluate((selector) => {
    const list = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect();
    const heading = [...document.querySelectorAll('[data-testid="user-query-heading"]')]
      .find((h) => h.getBoundingClientRect().bottom > list.top);
    return {
      text: heading?.textContent?.match(/^[^?]*\?/)?.[0] ?? '',
      offset: heading ? heading.getBoundingClientRect().top - list.top : NaN,
    };
  }, SCROLLER);
}

/** Resolves once the list has not moved for 10 animation frames: smooth scrolls and corrections are done. */
async function settled(page: Page) {
  await page.evaluate((selector) => new Promise<void>((resolve) => {
    let last = -1;
    let still = 0;
    const tick = () => {
      const top = document.querySelector<HTMLElement>(selector)?.scrollTop ?? -1;
      still = top === last ? still + 1 : 0;
      last = top;
      if (still >= 10) resolve();
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }), SCROLLER);
}

/** The question containing `text` ends up, and stays, at the top edge of the list. */
async function expectAtTop(page: Page, text: string) {
  await expect.poll(async () => Math.abs((await measure(page, text)).offset)).toBeLessThanOrEqual(2);
  await settled(page);
  expect(Math.abs((await measure(page, text)).offset)).toBeLessThanOrEqual(2);
}

/** The list ends up, and stays, at scrollTop `top`. */
async function expectScrollTop(page: Page, top: number) {
  await expect.poll(async () => Math.abs((await measure(page)).top - top)).toBeLessThanOrEqual(1);
  await settled(page);
  expect(Math.abs((await measure(page)).top - top)).toBeLessThanOrEqual(1);
}

async function openChat(page: Page, chat: string, lastTurn: number) {
  await page.goto(`/chat/?conversationId=${chat}`);
  await expect(page.getByText(question(chat, lastTurn))).toBeVisible({ timeout: 20_000 });
  await expectAtTop(page, question(chat, lastTurn));
}

/** Opens `chat` (15 turns, so its first page leaves 5 older turns) and holds the request for the older page. */
async function openWithOlderPage(page: Page, chat: string) {
  await mockApp(page, new Map([[chat, turns(chat, 1, 15)]]));
  let release!: () => void;
  let asked!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const requested = new Promise<void>((resolve) => { asked = resolve; });
  await page.route((url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${chat}` && url.searchParams.get('page') === '2', async (route) => {
    asked();
    await held;
    return route.fallback();
  });
  await openChat(page, chat, 15);
  // Start from 200 px, above the 120 px at which the list asks for older messages.
  await wheel(page, 200 - (await measure(page)).top);
  await expectScrollTop(page, 200);
  return { requested, release };
}

async function expectOlderPageShown(page: Page, chat: string) {
  await expect(page.getByTestId('user-query-heading').filter({ hasText: question(chat, 1) })).toBeAttached();
  await settled(page);
}

test.describe('Chat — message list scrolling (mocked backend)', () => {
  test('opening a conversation puts its last question at the top of the list', async ({ page }) => {
    const chat = 'scroll-open';
    await mockApp(page, new Map([[chat, turns(chat, 1, 6)]]));
    await openChat(page, chat, 6);
    // It scrolled to get there: the earlier turns are above the list's top edge.
    expect((await measure(page)).top).toBeGreaterThan(0);
    expect((await measure(page, question(chat, 5))).offset).toBeLessThan(0);
  });

  test('a sent question goes to the top of the list, also when the reader had scrolled up, and stays there when the answer ends', async ({ page }) => {
    const chat = 'scroll-send';
    const chats = new Map([[chat, turns(chat, 1, 6)]]);
    await mockApp(page, chats);
    await openChat(page, chat, 6);
    await wheel(page, -600);
    await expect.poll(async () => (await measure(page, question(chat, 6))).offset).toBeGreaterThan(300);
    await settled(page);

    await holdSseStream(page, `/api/v1/conversations/${chat}/messages/stream`, buildAguiPartialSseBody(chat, streamedLine(chat, 1)));
    await send(page, question(chat, 7));
    await expect(page.getByText(streamedLine(chat, 1))).toBeVisible();
    await expectAtTop(page, question(chat, 7));

    // The answer streams on, then ends; the backend stores what was streamed.
    await grow(page, chat, 2, 2);
    chats.set(chat, [...chats.get(chat)!, ...buildAguiConversation({
      conversationId: chat,
      userMessageId: `${chat}-q7`,
      botMessageId: `${chat}-a7`,
      question: question(chat, 7),
      answer: `${streamedLine(chat, 1)}\n\n${streamedLine(chat, 2)}`,
      modelInfo: MODEL,
    }).messages]);
    await push(page, finishFrames(chat, chats.get(chat)!));
    await runFinished(page);
    await expectAtTop(page, question(chat, 7));
  });

  test('while an answer streams, the list keeps its question at the top, then follows its end', async ({ page }) => {
    const chat = 'scroll-follow';
    await mockApp(page, new Map([[chat, turns(chat, 1, 6)]]));
    await openChat(page, chat, 6);
    await holdSseStream(page, `/api/v1/conversations/${chat}/messages/stream`, buildAguiPartialSseBody(chat, streamedLine(chat, 1)));
    await send(page, question(chat, 7));
    await expect(page.getByText(streamedLine(chat, 1))).toBeVisible();
    await expectAtTop(page, question(chat, 7));

    // Taller than the list: its end stays in view as it grows.
    await grow(page, chat, 2, 20);
    await expect.poll(async () => (await measure(page)).fromBottom).toBeLessThanOrEqual(2);
    const { top } = await measure(page);
    await grow(page, chat, 21, 30);
    await expect.poll(async () => (await measure(page)).fromBottom).toBeLessThanOrEqual(2);
    expect((await measure(page)).top).toBeGreaterThan(top);
  });

  test('a wheel up while an answer streams stops the list following it', async ({ page }) => {
    const chat = 'scroll-optout';
    await mockApp(page, new Map([[chat, turns(chat, 1, 6)]]));
    await openChat(page, chat, 6);
    await holdSseStream(page, `/api/v1/conversations/${chat}/messages/stream`, buildAguiPartialSseBody(chat, streamedLine(chat, 1)));
    await send(page, question(chat, 7));
    await grow(page, chat, 2, 20);
    await expect.poll(async () => (await measure(page)).fromBottom).toBeLessThanOrEqual(2);

    await wheel(page, -400);
    await expect.poll(async () => (await measure(page)).fromBottom).toBeGreaterThan(300);
    await settled(page);
    const { top } = await measure(page);
    await grow(page, chat, 21, 40);
    await settled(page);
    const after = await measure(page);
    expect(Math.abs(after.top - top)).toBeLessThanOrEqual(1);
    expect(after.fromBottom).toBeGreaterThan(300);
  });

  test('loading older messages keeps the reader where they were', async ({ page }) => {
    const chat = 'scroll-older';
    const { requested, release } = await openWithOlderPage(page, chat);
    await wheel(page, -140);
    await requested;
    await settled(page);
    const before = await firstVisibleQuestion(page);
    expect((await measure(page)).top).toBeGreaterThan(0);

    release();
    await expectOlderPageShown(page, chat);
    expect(Math.abs((await measure(page, before.text)).offset - before.offset)).toBeLessThanOrEqual(2);
  });

  test('loading older messages at the very top of the list moves the content down', async ({ page }) => {
    const chat = 'scroll-older-top';
    const { requested, release } = await openWithOlderPage(page, chat);
    await wheel(page, -1000);
    await requested;
    await settled(page);
    const before = await firstVisibleQuestion(page);
    expect((await measure(page)).top).toBe(0);

    release();
    await expectOlderPageShown(page, chat);
    // Today's behaviour: the list relies on native scroll anchoring, which does
    // not act at scrollTop 0, so the older page pushes the reader's place out
    // of view and the oldest loaded question shows instead.
    expect((await measure(page)).top).toBe(0);
    expect((await firstVisibleQuestion(page)).text).toBe(question(chat, 1));
    expect((await measure(page, before.text)).offset).toBeGreaterThan((await measure(page)).height);
  });

  test('switching to another conversation and back: the place is lost the first time, restored once both are loaded', async ({ page }) => {
    await mockApp(page, new Map([['scroll-a', turns('scroll-a', 1, 6)], ['scroll-b', turns('scroll-b', 1, 4)]]));
    await openChat(page, 'scroll-a', 6);
    const opened = (await measure(page)).top;
    await wheel(page, -500);
    await expectScrollTop(page, opened - 500);

    await page.getByText('Recents', { exact: true }).click();
    await page.locator('a', { hasText: 'scroll-b' }).click();
    await expectAtTop(page, question('scroll-b', 4));
    const bOpened = (await measure(page)).top;
    await page.locator('a', { hasText: 'scroll-a' }).click();
    // Today's behaviour: the list unmounts while a conversation that is not
    // loaded yet loads, so the place left in the first one is never saved and
    // it opens at its last question again.
    await expectAtTop(page, question('scroll-a', 6));
    expect(Math.abs((await measure(page)).top - opened)).toBeLessThanOrEqual(1);

    // Both are loaded now: each comes back exactly where it was left.
    await wheel(page, -700);
    await expectScrollTop(page, opened - 700);
    await page.locator('a', { hasText: 'scroll-b' }).click();
    await expect(page.getByText(question('scroll-b', 4))).toBeVisible();
    await expectScrollTop(page, bOpened);
    await page.locator('a', { hasText: 'scroll-a' }).click();
    await expect(page.getByText(question('scroll-a', 6))).toBeVisible();
    await expectScrollTop(page, opened - 700);
  });
});
