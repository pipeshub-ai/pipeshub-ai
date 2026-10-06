import { test, expect } from './support/two-users.fixture';
import { AI_ROUTES, fake } from './support/stack';
import { composer } from './support/chat-ui';
import { COLLAB_FLAGS, setFlags } from './support/collab.helper';

/**
 * PH12-06 (the part this lane can run): with every collaboration flag OFF the chat page behaves as it did before the
 * feature. The full `tests/e2e/chat/*.spec.ts` suite needs a real AI model and the default harness (CI); here the
 * parts that do not need a model run against the lane with all three flags off: the composer is the plain textarea the
 * old specs select, typing and the send button work, a send reaches the AI backend with the old body, and none of the
 * collaboration chrome or requests appears. API parity for the same flags-off state is J-02
 * (`integration-tests/collaborative-chats/integration_test_j02_flag_off_parity.py`).
 */
test.describe.configure({ mode: 'serial' });

const off = Object.fromEntries(COLLAB_FLAGS.map((k) => [k, false]));
const on = Object.fromEntries(COLLAB_FLAGS.map((k) => [k, true]));

test.beforeAll(async () => {
  await setFlags(off);
});
test.afterAll(async () => {
  await setFlags(on);
});

const COLLAB_CHROME = [
  'author-chip',
  'audience-notice',
  'busy-banner',
  'read-only-banner',
  'note-bubble',
  'human-message',
  'reply-message',
  'message-timeline',
  'mention-chip',
  'agent-draft-card',
  'changed-notice',
] as const;
const COLLAB_REQUEST = /\/(feed|readiness|collaborators|collaboration-settings|notes|mentionables)(\?|$)|\/authz\/explain/;

test('PH12-06: the new-chat page is the plain textarea composer and accepts typing', { tag: '@collab' }, async ({ users }) => {
  const { page } = users.a;
  await page.goto('/chat/');
  await page.waitForSelector('[data-testid="chat-composer"]', { timeout: 30_000 });
  const textarea = page.locator('textarea[data-testid="chat-composer"]');
  await expect(textarea).toBeVisible();
  await textarea.click();
  await textarea.fill('Hello from Playwright');
  await expect(textarea).toHaveValue('Hello from Playwright');
  await textarea.fill('');
  await expect(textarea).toHaveValue('');
  // The send button is inert while the box is empty, as the old specs expect.
  await expect(page.getByRole('button', { name: 'Send message' })).toBeDisabled();
});

test('PH12-06: a send creates a chat with the old body, shows the answer, and no collaboration request or chrome appears', { tag: '@collab' }, async ({ users }) => {
  const { page } = users.a;
  const requests: string[] = [];
  let sentBody: Record<string, unknown> | undefined;
  page.on('request', (r) => {
    if (COLLAB_REQUEST.test(r.url())) requests.push(`${r.method()} ${r.url()}`);
    if (r.method() === 'POST' && /\/conversations\/(stream|[0-9a-f]{24}\/messages\/stream)$/.test(r.url())) {
      sentBody = r.postDataJSON() as Record<string, unknown>;
    }
  });

  await page.goto('/chat/');
  await composer(page).fill('Plain question, flags off');
  const mark = await fake.mark();
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByText('Fake answer')).toBeVisible({ timeout: 30_000 });
  expect(await fake.requests(AI_ROUTES, mark)).toHaveLength(1);

  expect(sentBody).toBeDefined();
  for (const key of ['clientMessageId', 'baseSeq', 'filesShared', 'shareToolResults', 'mentions', 'resume']) {
    expect(sentBody, `old send body must not carry ${key}`).not.toHaveProperty(key);
  }
  for (const id of COLLAB_CHROME) await expect(page.getByTestId(id), id).toHaveCount(0);
  await expect(page.locator('textarea[data-testid="chat-composer"]')).toBeVisible();
  // No poll, readiness or access call went out for the whole turn.
  expect(requests).toEqual([]);
});

test('PH12-06: with the flags off the collaboration routes answer 404 and a chat keeps its owner-only share', { tag: '@collab' }, async ({ users }) => {
  const { a, b } = users;
  const chat = await a.api.startChat('e2e-collab-flag-off first question');
  expect((await a.api.call('POST', `/api/v1/conversations/${chat}/notes`, { query: 'x', mentions: [], clientMessageId: 'n' })).status).toBe(404);
  expect((await a.api.call('GET', `/api/v1/conversations/${chat}/collaborators`)).status).toBe(404);
  expect((await b.api.getChat(chat)).status).toBe(404);

  await a.page.goto(`/chat/?conversationId=${chat}`);
  await expect(a.page.getByTestId('user-query-heading').first()).toBeVisible({ timeout: 30_000 });
  await expect(a.page.locator('textarea[data-testid="chat-composer"]')).toBeVisible();
  for (const id of COLLAB_CHROME) await expect(a.page.getByTestId(id), id).toHaveCount(0);
  await expect(a.page.getByRole('button', { name: 'Who can access this chat' })).toHaveCount(0);
});
