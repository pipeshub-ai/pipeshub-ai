/**
 * chat-edit-question.spec.ts
 *
 * Editing the last question of a chat: the edit icon on the question, the
 * "Edit query" composer, the files of the question, and what is sent to the
 * regenerate endpoint.
 *
 * The chat is opened from a mocked conversation and the regenerate endpoint is
 * mocked too, so no model or live conversation is needed. The mock plays the
 * server's part: after a regenerate it stores the edited question, so the page's
 * reload of the conversation shows it.
 */

import { test, expect } from '../fixtures/base.fixture';
import type { Page } from '@playwright/test';
import { buildAguiConversation, buildAguiSseBody } from './agui-sse-builder';

const CONV_ID = 'conv-e2e-edit-001';
const BOT_ID = 'msg-bot-e2e-edit-002';
const USER_ID = 'msg-user-e2e-edit-001';

const QUESTION = 'Explain machine learning in simple terms.';
const ANSWER = 'Machine learning is when systems learn patterns from data.';
const EDITED_QUESTION = 'Explain deep learning in simple terms.';
const EDITED_ANSWER = 'Deep learning uses many-layered neural networks.';

const MODEL_INFO = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'internal_search',
  modelFriendlyName: 'GPT-4o mini',
};

const FILE = {
  recordId: 'rec-e2e-report',
  recordName: 'report.pdf',
  mimeType: 'application/pdf',
  extension: 'pdf',
  virtualRecordId: 'v-e2e-report',
};

type Stored = ReturnType<typeof buildAguiConversation>;

function conversation(question: string, answer: string, files: unknown[] = []): Stored {
  const stored = buildAguiConversation({
    conversationId: CONV_ID,
    userMessageId: USER_ID,
    botMessageId: BOT_ID,
    question,
    answer,
    modelInfo: MODEL_INFO,
  });
  stored.messages[0] = { ...stored.messages[0], attachments: files };
  return stored;
}

interface Regenerate {
  url: string;
  body: Record<string, unknown>;
}

/**
 * Opens the chat from `initial` and answers the regenerate call the way the server does: the edited
 * question (and files, when sent) replace the stored ones, and the stream ends with the new answer.
 */
async function openChat(page: Page, initial: Stored) {
  let stored = initial;
  const regenerates: Regenerate[] = [];
  const attachmentDeletes: string[] = [];

  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
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
      }),
    }),
  );

  await page.route('**/api/v1/conversations?*', (route) => {
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

  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${CONV_ID}`,
    (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation: { ...stored, id: stored._id, access: { isOwner: true, accessLevel: 'OWNER' } },
          filters: {},
          meta: {},
        }),
      });
    },
  );

  await page.route(`**/api/v1/conversations/${CONV_ID}/message/*/regenerate`, (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as Record<string, unknown>;
    regenerates.push({ url: route.request().url(), body });
    const question = typeof body.query === 'string' ? body.query : QUESTION;
    const files = Array.isArray(body.attachments)
      ? body.attachments
      : ((stored.messages[0] as { attachments?: unknown[] }).attachments ?? []);
    stored = conversation(question, EDITED_ANSWER, files);
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: buildAguiSseBody({
        conversationId: CONV_ID,
        userMessageId: USER_ID,
        botMessageId: BOT_ID,
        question,
        answer: EDITED_ANSWER,
        modelInfo: MODEL_INFO,
      }),
    });
  });

  // A file removed from a question must never be deleted from storage.
  await page.route('**/api/v1/conversations/attachments/*', (route) => {
    if (route.request().method() === 'DELETE') attachmentDeletes.push(route.request().url());
    return route.fulfill({ status: 204, body: '' });
  });

  await page.goto(`/chat/?conversationId=${CONV_ID}`);
  await expect(page.getByText(initial.messages[1].content as string).first()).toBeVisible({ timeout: 30_000 });

  return { regenerates, attachmentDeletes };
}

/** The question is the heading of its turn, next to its copy and edit icons. */
const questionHeading = (page: Page, text: string) =>
  page.getByRole('heading', { level: 1 }).filter({ hasText: text });

const editButton = (page: Page) => page.getByRole('button', { name: 'Edit', exact: true });
const composer = (page: Page) => page.locator('textarea').last();

/** The icon fades in on hover, so the pointer has to be over the question first. */
async function startEdit(page: Page, question: string) {
  await questionHeading(page, question).first().hover();
  await editButton(page).click();
  await expect(page.getByText('Edit query')).toBeVisible();
}

test.describe('Chat — editing the last question', () => {
  test('sends the edited question, and shows it with the new answer after the reload', async ({ page }) => {
    const { regenerates, attachmentDeletes } = await openChat(page, conversation(QUESTION, ANSWER));

    await startEdit(page, QUESTION);
    await expect(composer(page)).toHaveValue(QUESTION);

    await composer(page).fill(EDITED_QUESTION);
    await composer(page).press('Enter');

    await expect(page.getByText(EDITED_ANSWER).first()).toBeVisible({ timeout: 20_000 });
    await expect(questionHeading(page, EDITED_QUESTION)).toBeVisible();
    await expect(questionHeading(page, QUESTION)).toHaveCount(0);
    await expect(page.getByText('Edit query')).toHaveCount(0);

    expect(regenerates).toHaveLength(1);
    expect(regenerates[0].url).toContain(`/conversations/${CONV_ID}/message/${BOT_ID}/regenerate`);
    expect(regenerates[0].body.query).toBe(EDITED_QUESTION);
    expect(regenerates[0].body).not.toHaveProperty('attachments');
    expect(attachmentDeletes).toEqual([]);
  });

  test('cancelling the edit sends nothing and leaves the question as it was', async ({ page }) => {
    const { regenerates } = await openChat(page, conversation(QUESTION, ANSWER));

    await startEdit(page, QUESTION);
    await composer(page).fill(EDITED_QUESTION);
    await composer(page).press('Escape');

    await expect(page.getByText('Edit query')).toHaveCount(0);
    await expect(composer(page)).toHaveValue('');
    await expect(questionHeading(page, QUESTION)).toBeVisible();
    expect(regenerates).toEqual([]);
  });

  test('an edit that changes nothing is a plain regenerate', async ({ page }) => {
    const { regenerates } = await openChat(page, conversation(QUESTION, ANSWER));

    await startEdit(page, QUESTION);
    await composer(page).press('Enter');

    await expect(page.getByText(EDITED_ANSWER).first()).toBeVisible({ timeout: 20_000 });
    expect(regenerates).toHaveLength(1);
    expect(regenerates[0].body).not.toHaveProperty('query');
    expect(regenerates[0].body).not.toHaveProperty('attachments');
  });

  test('only the newest question can be edited', async ({ page }) => {
    const first = conversation('An earlier question', 'An earlier answer.');
    const second = buildAguiConversation({
      conversationId: CONV_ID,
      userMessageId: 'msg-user-e2e-edit-003',
      botMessageId: 'msg-bot-e2e-edit-004',
      question: QUESTION,
      answer: ANSWER,
      modelInfo: MODEL_INFO,
    });
    await openChat(page, { ...first, messages: [...first.messages, ...second.messages] });

    await expect(page.getByText(ANSWER).first()).toBeVisible();
    await expect(editButton(page)).toHaveCount(1);
  });
});

test.describe('Chat — editing the files of the question', () => {
  test('keeps the stored files when only the text changes', async ({ page }) => {
    const { regenerates } = await openChat(page, conversation(QUESTION, ANSWER, [FILE]));

    await startEdit(page, QUESTION);
    await expect(page.getByText('report.pdf').first()).toBeVisible();

    await composer(page).fill(EDITED_QUESTION);
    await composer(page).press('Enter');

    await expect(page.getByText(EDITED_ANSWER).first()).toBeVisible({ timeout: 20_000 });
    expect(regenerates[0].body.query).toBe(EDITED_QUESTION);
    expect(regenerates[0].body).not.toHaveProperty('attachments');
  });

  test('removing a file sends the new list and never deletes the stored file', async ({ page }) => {
    const { regenerates, attachmentDeletes } = await openChat(page, conversation(QUESTION, ANSWER, [FILE]));

    await startEdit(page, QUESTION);
    await page.getByRole('button', { name: 'Remove report.pdf' }).click();
    await composer(page).press('Enter');

    await expect(page.getByText(EDITED_ANSWER).first()).toBeVisible({ timeout: 20_000 });
    expect(regenerates).toHaveLength(1);
    expect(regenerates[0].body.attachments).toEqual([]);
    expect(regenerates[0].body.query).toBe(QUESTION);
    expect(attachmentDeletes).toEqual([]);
  });

  test('cancelling the edit keeps the stored file on the question and deletes nothing', async ({ page }) => {
    const { regenerates, attachmentDeletes } = await openChat(page, conversation(QUESTION, ANSWER, [FILE]));

    await startEdit(page, QUESTION);
    await page.getByRole('button', { name: 'Remove report.pdf' }).click();
    await composer(page).press('Escape');

    await expect(page.getByText('Edit query')).toHaveCount(0);
    await expect(page.getByText('report.pdf').first()).toBeVisible();
    expect(regenerates).toEqual([]);
    expect(attachmentDeletes).toEqual([]);
  });
});
