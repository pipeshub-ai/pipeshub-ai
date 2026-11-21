/**
 * toast-under-pointer.spec.ts
 *
 * A toast that comes up under the resting pointer must not hold the button the
 * pointer goes to next, as a "connector created" toast could hold Save
 * configuration in a setup panel's footer. The toast here is the app's own: the
 * thanks a like on an answer raises, asked for from the keyboard so the pointer
 * stays where it rests, over a button pinned in the toast's corner. It closes on
 * its clock while the pointer moves on it, pauses once the pointer has left it
 * and come back, and closes at a press on its text.
 *
 * Every request is answered by page.route, the app shell's included, and the
 * session is a made-up token, so the spec needs no backend, sign-in or model:
 *   npx playwright test --project=authenticated --no-deps tests/e2e/chat/toast-under-pointer.spec.ts
 */

import type { Page, Route } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';

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

const CHAT = 'toast-chat';
const QUESTION = 'Which figures changed this quarter?';
const THANKS = 'Thank you for your feedback';

function json(route: Route, body: unknown) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

/** The chat as the API returns it, titled with its id. */
const conversation = (messages: unknown[]) => ({
  ...buildAguiConversation({ conversationId: CHAT, userMessageId: `${CHAT}-title`, question: CHAT, modelInfo: MODEL }),
  messages,
});

/** Opens one finished chat. The shell's routes and the chat's are answered here; anything else gets an empty body. */
async function openChat(page: Page) {
  const { messages } = buildAguiConversation({
    conversationId: CHAT,
    userMessageId: `${CHAT}-q1`,
    botMessageId: `${CHAT}-a1`,
    question: QUESTION,
    answer: 'Revenue and headcount changed. The team checked each figure against its source.',
    modelInfo: MODEL,
  });
  await page.addInitScript((token) => {
    localStorage.setItem('jwt_access_token', token);
    localStorage.setItem('jwt_refresh_token', token);
    // A build may keep its tokens in this map instead.
    localStorage.setItem('pipeshub_org_tokens', JSON.stringify({ 'org-e2e': { accessToken: token, refreshToken: token } }));
  }, TOKEN);
  // Registered first, so every route below wins.
  await page.route('**/api/**', (route) => json(route, {}));
  await page.route('**/api/v1/health{,/services}', (route) => json(route, { status: 'healthy', services: {} }));
  await page.route('**/api/v1/users/user-e2e', (route) =>
    json(route, { fullName: 'E2E User', email: 'e2e@example.com', hasLoggedIn: true }));
  await page.route('**/api/v1/userGroups/users/*', (route) => json(route, []));
  await page.route('**/api/v1/userGroups/users/*/permissions', (route) => json(route, { permissions: {} }));
  await page.route('**/api/v1/org/onboarding-status', (route) => json(route, { status: 'configured' }));
  await page.route(/\/api\/v1\/(users\/dp|org\/logo)$/, (route) => route.fulfill({ status: 204 }));
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) => json(route, LLMS));
  await page.route((url) => url.pathname.replace(/\/$/, '') === '/api/v1/conversations', (route) => {
    const owned = new URL(route.request().url()).searchParams.get('source') !== 'shared';
    const conversations = owned ? [{ ...conversation([]), isOwner: true }] : [];
    return json(route, {
      conversations,
      source: owned ? 'owned' : 'shared',
      pagination: { page: 1, limit: 10, totalCount: conversations.length, totalPages: 1, hasNextPage: false, hasPrevPage: false },
    });
  });
  await page.route((url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${CHAT}`, (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return json(route, {
      conversation: {
        ...conversation(messages),
        id: CHAT,
        access: { isOwner: true, accessLevel: 'OWNER' },
        pagination: { page: 1, limit: 20, totalCount: messages.length, totalPages: 1, hasNextPage: false, hasPrevPage: false },
      },
      filters: {},
      meta: {},
    });
  });
  await page.goto(`/chat/?conversationId=${CHAT}`);
  await expect(page.getByText(QUESTION)).toBeVisible({ timeout: 20_000 });
}

/** A button pinned in the toast's corner, as a panel's footer button sits there, counting its clicks. Returns its middle. */
async function pinButtonInTheCorner(page: Page) {
  await page.evaluate(() => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Save configuration';
    button.dataset.testid = 'corner-button';
    button.dataset.clicks = '0';
    Object.assign(button.style, { position: 'fixed', right: '40px', bottom: '24px', width: '200px', height: '28px', zIndex: '1000' });
    button.addEventListener('click', () => {
      button.dataset.clicks = String(Number(button.dataset.clicks) + 1);
    });
    document.body.append(button);
  });
  const box = await page.getByTestId('corner-button').boundingBox();
  if (!box) throw new Error('the corner button is not on the page');
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Likes the answer from the keyboard, so the pointer stays where it rests; the app thanks them in a toast. */
async function likeFromTheKeyboard(page: Page) {
  await page.getByRole('button', { name: 'Like', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: 'Excellent answer' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText(THANKS)).toBeVisible();
}

/** Whether the toast is what the pointer at `at` is over. */
function toastIsAt(page: Page, at: { x: number; y: number }) {
  return page.evaluate(({ x, y }) => !!document.elementFromPoint(x, y)?.closest('[data-ph-toast-region]'), at);
}

test.describe('A toast that comes up over a button, under the resting pointer', () => {
  test('closes on its clock while the pointer moves on it, and the next click reaches the button', async ({ page }) => {
    await openChat(page);
    const at = await pinButtonInTheCorner(page);
    await page.mouse.move(at.x, at.y);
    await likeFromTheKeyboard(page);
    await expect.poll(() => toastIsAt(page, at)).toBe(true);

    // The hand moves on toward the button, over the toast.
    await page.mouse.move(at.x + 6, at.y + 3, { steps: 4 });
    await page.mouse.move(at.x, at.y, { steps: 4 });
    // The toast's 4 s run out with the pointer on it, and the click goes to the button.
    await page.getByTestId('corner-button').click({ timeout: 8_000 });
    await expect(page.getByTestId('corner-button')).toHaveAttribute('data-clicks', '1');
    await expect(page.getByText(THANKS)).toBeHidden();
  });

  test('pauses once the pointer has left it and come back, and goes when the pointer leaves again', async ({ page }) => {
    await openChat(page);
    const at = await pinButtonInTheCorner(page);
    await page.mouse.move(at.x, at.y);
    await likeFromTheKeyboard(page);
    await expect.poll(() => toastIsAt(page, at)).toBe(true);

    await page.mouse.move(at.x - 500, at.y - 300, { steps: 5 });
    await page.mouse.move(at.x, at.y, { steps: 5 });
    // Held well past its 4 s.
    await page.waitForTimeout(6_000);
    await expect(page.getByText(THANKS)).toBeVisible();
    await page.mouse.move(at.x - 500, at.y - 300, { steps: 5 });
    await expect(page.getByText(THANKS)).toBeHidden({ timeout: 6_000 });
  });

  test('closes at a press on its text, and the next click reaches the button at once', async ({ page }) => {
    await openChat(page);
    const at = await pinButtonInTheCorner(page);
    await page.mouse.move(at.x, at.y);
    await likeFromTheKeyboard(page);
    await expect.poll(() => toastIsAt(page, at)).toBe(true);

    await page.getByText(THANKS).click();
    await expect(page.getByText(THANKS)).toBeHidden({ timeout: 1_000 });
    await page.getByTestId('corner-button').click({ timeout: 1_000 });
    await expect(page.getByTestId('corner-button')).toHaveAttribute('data-clicks', '1');
  });
});
