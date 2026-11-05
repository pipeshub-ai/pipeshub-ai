/**
 * Temporary chat e2e (mocked backend).
 *
 * Toggle on, send, assert the stream is flagged temporary, the thread stays
 * out of the URL and sidebar, refresh restores it, and leaving hard-deletes it.
 */
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation, buildAguiSseBody } from './agui-sse-builder';

const CONV_ID = 'conv-e2e-temp-001';
const OTHER_ID = 'conv-e2e-kept-001';
const USER_ID = 'user-e2e-temp';
const MSG_USER_ID = 'msg-user-temp-001';
const MSG_BOT_ID = 'msg-bot-temp-001';
const QUESTION = 'What is the payroll number?';
const ANSWER = 'I will not put that in history.';

function unsignedJwt(userId: string): string {
  const encode = (value: object) =>
    Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${encode({ alg: 'none', typ: 'JWT' })}.${encode({
    userId,
    orgId: 'org-e2e',
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}.sig`;
}

const ACCESS_TOKEN = unsignedJwt(USER_ID);

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
  chatMode: 'quick',
  modelFriendlyName: 'GPT-4o mini',
};

const EMPTY_LIST = {
  conversations: [] as Array<Record<string, unknown>>,
  source: 'owned',
  pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
};

function listWithKept() {
  const now = new Date().toISOString();
  return {
    ...EMPTY_LIST,
    conversations: [
      {
        _id: OTHER_ID,
        userId: USER_ID,
        orgId: 'org-e2e',
        title: 'Kept regular chat',
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
    pagination: { ...EMPTY_LIST.pagination, totalCount: 1, totalPages: 1 },
  };
}

test.use({
  storageState: {
    cookies: [],
    origins: [
      {
        origin: process.env.BASE_URL || 'http://localhost:3001',
        localStorage: [
          { name: 'jwt_access_token', value: ACCESS_TOKEN },
          { name: 'jwt_refresh_token', value: ACCESS_TOKEN },
        ],
      },
    ],
  },
});

async function mockBaseline(page: import('@playwright/test').Page, list = EMPTY_LIST) {
  await page.route('**/api/v1/health/services', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'healthy',
        services: { query: 'healthy', connector: 'healthy', indexing: 'healthy', docling: 'healthy' },
      }),
    }),
  );
  await page.route('**/api/v1/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'healthy',
        services: { redis: 'healthy', mongodb: 'healthy', messageBroker: 'healthy', graphDb: 'healthy', vectorDb: 'healthy' },
      }),
    }),
  );
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ featureFlags: {} }),
    }),
  );
  await page.route('**/api/v1/org/onboarding-status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'configured' }),
    }),
  );
  await page.route(`**/api/v1/users/${USER_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        _id: USER_ID,
        userId: USER_ID,
        firstName: 'E2E',
        lastName: 'Temp',
        fullName: 'E2E Temp',
        email: 'e2e-temp@example.com',
        role: 'member',
        hasLoggedIn: true,
      }),
    }),
  );
  await page.route(`**/api/v1/userGroups/users/${USER_ID}`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  );
  await page.route('**/api/v1/users/dp', (route) => route.fulfill({ status: 204, body: '' }));
  await page.route('**/api/v1/agents**', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agents: [],
        pagination: { currentPage: 1, limit: 20, totalItems: 0, totalPages: 0, hasNext: false, hasPrev: false },
      }),
    });
  });
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_LLMS) }),
  );
  await page.route('**/api/v1/conversations*', (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'GET' && !url.pathname.includes(`/${CONV_ID}`)) {
      const shared = url.searchParams.get('source') === 'shared';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(shared ? EMPTY_LIST : list),
      });
    }
    return route.continue();
  });
  await page.route('**/api/v1/users/by-ids', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });
}

function storedConversation() {
  return buildAguiConversation({
    conversationId: CONV_ID,
    userMessageId: MSG_USER_ID,
    botMessageId: MSG_BOT_ID,
    question: QUESTION,
    answer: ANSWER,
    modelInfo: MOCK_MODEL_INFO,
  });
}

async function mockTempConversationGet(page: import('@playwright/test').Page) {
  const conversation = storedConversation();
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${CONV_ID}`,
    (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation: {
            ...conversation,
            id: conversation._id,
            isTemporary: true,
            access: { isOwner: true, accessLevel: 'OWNER' },
          },
          filters: {},
          meta: {},
        }),
      });
    },
  );
}

async function mockStream(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/conversations/stream', (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: buildAguiSseBody({
        conversationId: CONV_ID,
        userMessageId: MSG_USER_ID,
        botMessageId: MSG_BOT_ID,
        question: QUESTION,
        answer: ANSWER,
        modelInfo: MOCK_MODEL_INFO,
        requestId: 'req-e2e-temp',
      }),
    });
  });
}

function tempToggle(page: import('@playwright/test').Page) {
  return page.getByRole('button', { name: /temporary or incognito chat|turn off temporary chat/i });
}

async function turnTemporaryOn(page: import('@playwright/test').Page) {
  const toggle = tempToggle(page);
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
}

async function sendQuestion(page: import('@playwright/test').Page) {
  const textarea = page.locator('textarea').last();
  await textarea.click();
  await textarea.fill(QUESTION);
  await textarea.press('Enter');
}

test.describe('Chat — temporary chat (mocked backend)', () => {
  test('toggle shows the banner and does not write a conversation URL', async ({ page }) => {
    await mockBaseline(page);
    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });

    await turnTemporaryOn(page);
    await expect(page.getByText(/won't be saved to your history/i)).toBeVisible();
    await expect(page).toHaveURL(/\/chat\/?(\?.*)?$/);
    expect(page.url()).not.toContain('conversationId');
  });

  test('first message sends temporary: true, stays off the sidebar and URL', async ({ page }) => {
    const streamBodies: unknown[] = [];
    await mockBaseline(page);
    await mockTempConversationGet(page);
    await page.route('**/api/v1/conversations/stream', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      streamBodies.push(route.request().postDataJSON());
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        body: buildAguiSseBody({
          conversationId: CONV_ID,
          userMessageId: MSG_USER_ID,
          botMessageId: MSG_BOT_ID,
          question: QUESTION,
          answer: ANSWER,
          modelInfo: MOCK_MODEL_INFO,
          requestId: 'req-e2e-temp',
        }),
      });
    });

    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await turnTemporaryOn(page);
    await sendQuestion(page);

    await expect(page.getByText(ANSWER).first()).toBeVisible({ timeout: 20_000 });
    expect(streamBodies[0]).toMatchObject({ query: QUESTION, temporary: true });
    expect(page.url()).not.toContain(CONV_ID);
    await expect(page.getByRole('link', { name: QUESTION })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /share/i })).toHaveCount(0);
  });

  test('refresh keeps the hidden thread', async ({ page }) => {
    await mockBaseline(page);
    await mockTempConversationGet(page);
    await mockStream(page);

    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await turnTemporaryOn(page);
    await sendQuestion(page);
    await expect(page.getByText(ANSWER).first()).toBeVisible({ timeout: 20_000 });

    await page.reload();
    await expect(page.getByText(QUESTION).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(ANSWER).first()).toBeVisible();
    expect(page.url()).not.toContain(CONV_ID);
    await expect(page.getByText(/won't be saved to your history/i)).toBeVisible();
  });

  test('opening another chat hard-deletes the temporary one', async ({ page }) => {
    const deletes: string[] = [];
    await mockBaseline(page, listWithKept());
    await mockTempConversationGet(page);
    await mockStream(page);
    await page.route(`**/api/v1/conversations/${OTHER_ID}**`, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            conversation: {
              _id: OTHER_ID,
              id: OTHER_ID,
              title: 'Kept regular chat',
              messages: [],
              access: { isOwner: true, accessLevel: 'OWNER' },
            },
            filters: {},
            meta: {},
          }),
        });
      }
      return route.continue();
    });
    await page.route(`**/api/v1/conversations/${CONV_ID}/temporary`, (route) => {
      deletes.push(route.request().method());
      return route.fulfill({ status: 204, body: '' });
    });

    await page.goto('/chat/');
    await page.waitForSelector('textarea', { timeout: 15_000 });
    await turnTemporaryOn(page);
    await sendQuestion(page);
    await expect(page.getByText(ANSWER).first()).toBeVisible({ timeout: 20_000 });

    await page.getByText('Recents').click();
    await page.getByRole('link', { name: 'Kept regular chat' }).first().click();
    await expect.poll(() => deletes).toContain('DELETE');
  });
});
