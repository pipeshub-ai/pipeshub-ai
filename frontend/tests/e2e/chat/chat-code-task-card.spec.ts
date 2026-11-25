/**
 * chat-code-task-card.spec.ts — U1 E2E-1, as CTL-4 shows it: an engine task's
 * frames on the chat stream are steps on the answer's timeline (its command,
 * with its output on demand) and its file under "Files changed" with its diff,
 * all reachable by keyboard; there is no code-task card. (E2E-2, stage 1's busy
 * refusal, went with stage 1: S1R.)
 *
 * The backend is mocked with page.route(); the `setup` project signs in
 * against a live stack, so this spec runs only with the e2e stack up
 * (`npm run test:e2e -- tests/e2e/chat/chat-code-task-card.spec.ts`). The card,
 * the reducer and the stream path are covered in vitest
 * (`files-changed.test.tsx`, `ctl4-engine-timeline.test.tsx`).
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';

const CONVERSATION_ID = 'conv-e2e-code-task-001';
const QUESTION = 'Write the Q3 revenue report';
const ANSWER = 'The report is attached.';
const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'quick', modelFriendlyName: 'GPT-4o mini' };
const DIFF = '--- a/out/report.md\n+++ b/out/report.md\n@@ -0,0 +1,2 @@\n+# Q3 revenue\n+Revenue grew 4%.\n';

function frame(type: string, fields: Record<string, unknown> = {}): string {
  return `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;
}

function progressValue(kind: string, text: string, data: Record<string, unknown> = {}): Record<string, unknown> {
  return { tool: 'code_task', taskId: 'ct-e2e', kind, text, data };
}

function body(task: string[], frames: Array<Record<string, unknown>>): string {
  const conversation = buildAguiConversation({
    conversationId: CONVERSATION_ID,
    userMessageId: 'msg-user-code-task',
    botMessageId: 'msg-bot-code-task',
    question: QUESTION,
    modelInfo: MODEL_INFO,
    answer: ANSWER,
  });
  // The answer as Node saves it: the engine's frames in its `engine_task` part.
  (conversation.messages[1] as Record<string, unknown>).parts = [
    { type: 'engine_task', taskId: 'ct-e2e', frames },
    { type: 'text', content: ANSWER, isFinal: true },
  ];
  return [
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId: CONVERSATION_ID, title: QUESTION } }),
    frame('RUN_STARTED', { threadId: CONVERSATION_ID, runId: 'req-e2e-code-task' }),
    ...task,
    frame('TEXT_MESSAGE_START', { messageId: 'msg_2', role: 'assistant' }),
    frame('TEXT_MESSAGE_CONTENT', { messageId: 'msg_2', delta: ANSWER }),
    frame('TEXT_MESSAGE_END', { messageId: 'msg_2' }),
    frame('RUN_FINISHED', { result: { conversation, meta: { requestId: 'req-e2e-code-task', duration: 1 } } }),
  ].join('');
}

const COMPLETED = [
  progressValue('started', QUESTION),
  progressValue('command', 'python report.py', { callId: 'c1' }),
  progressValue('output', 'wrote out/report.md\n', { callId: 'c1', stream: 'stdout', truncated: false }),
  progressValue('command_done', '', { callId: 'c1', exitCode: 0, status: 'completed' }),
  progressValue('file', 'out/report.md', { change: 'added' }),
  progressValue('artifact', 'report.md', { artifactId: 'art-e2e', lifecycle: 'draft', sizeBytes: 34 }),
  progressValue('version', '2', { revision: 2, reason: 'turn_end', files: 1, changed: 1, omitted: 0 }),
  progressValue('diff', 'out/report.md', { change: 'added', sizeBytes: 34, diff: DIFF, truncated: false }),
  progressValue('artifact', 'report.md', { artifactId: 'art-e2e', lifecycle: 'final', sizeBytes: 34 }),
  progressValue('finished', 'Revenue grew 4%. Wrote report.md.', { success: true }),
];

async function mockBackend(page: Page, frames: Array<Record<string, unknown>>): Promise<void> {
  const task = frames.map((value) => frame('CUSTOM', { name: 'engine_progress', value }));
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ featureFlags: {}, agentHarness: null }) }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'success',
        message: 'Success',
        models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }],
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
      : route.fallback(),
  );
  await page.route('**/api/v1/users/by-ids', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : route.fallback(),
  );
  await page.route('**/api/v1/conversations/stream', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: body(task, frames) })
      : route.fallback(),
  );
}

async function ask(page: Page): Promise<void> {
  await page.goto('/chat/');
  const textarea = page.locator('textarea').last();
  await textarea.waitFor({ timeout: 15_000 });
  await textarea.fill(QUESTION);
  await textarea.press('Enter');
}

test.describe('Chat — an engine task on the answer\'s timeline (U1, CTL-4, needs the e2e stack)', () => {
  test('E2E-1: the command and the changed file are on the timeline; output and diff open by keyboard', async ({ page }) => {
    await mockBackend(page, COMPLETED);
    await ask(page);

    const summary = page.getByTestId('activity-summary');
    await expect(summary).toContainText('Ran 1 command', { timeout: 15_000 });
    await expect(summary).toContainText('Created report.md');
    await expect(page.getByText(ANSWER)).toBeVisible();
    await expect(page.getByRole('region', { name: 'Code task' })).toHaveCount(0);
    if ((await summary.getAttribute('aria-expanded')) === 'false') {
      await summary.focus();
      await page.keyboard.press('Enter');
    }

    const command = page.getByRole('button', { name: /python report\.py/ });
    await command.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText('wrote out/report.md')).toBeVisible();
    // CTRESULTS: the output reads in place; a short one whole, with no toggle and no detour to View activity.
    await expect(page.getByRole('button', { name: /Show the whole output|Show only the last lines/ })).toHaveCount(0);
    await expect(page.getByText(/in View activity/)).toHaveCount(0);

    const file = page.getByRole('list', { name: 'Files changed' }).getByRole('button', { name: /out\/report\.md/ });
    await expect(file).toContainText('added');
    await expect(file).toContainText('+2 −0');
    await file.focus();
    await page.keyboard.press('Space');
    const diff = page.getByRole('region', { name: 'Changes in out/report.md' });
    await expect(diff).toContainText('+Revenue grew 4%.');
    await expect(page.getByText('Lines added: 2. Lines removed: 0.')).toBeVisible();
  });
});
