/**
 * chat-timeline.spec.ts — CTL-3: a durable loop run's answer shows its work the way Claude does. While the run
 * works the steps are open under a live summary, with the plan (UXR-B1's `plan` frames) on top; when it ends they
 * fold into the summary line. "View activity" is on the answer, and the drawer stays shut until it is clicked.
 * The frames are Node's projection of `agent_steered_during_tool` (contracts/harness/vectors/journals) with two
 * plan frames added; routes are mocked as in chat-durable-run-ui.spec.ts.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';
import { FakeRunStream, MODEL_INFO, mockInFlightConversation, openConversation, type SseFrame } from './durable-conversation.mock';

const JOURNALS = join(__dirname, '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals');
const SOURCE = JSON.parse(readFileSync(join(JOURNALS, 'agent_steered_during_tool.frames.json'), 'utf8')) as {
  frames: Array<Record<string, unknown>>;
};
const SESSION_ID = 'conv-ctl3-timeline';
const RUN_ID = 'run-golden';
const QUESTION = 'Export the Q3 report';
const ANSWER = 'The Q3 report is exported. Q4 is not closed yet, so it is not in it.';

const plan = (first: string, second: string) => ({
  type: 'CUSTOM',
  name: 'plan',
  runId: RUN_ID,
  value: {
    runId: RUN_ID,
    currentTaskId: second === 'in_progress' ? 't2' : first === 'in_progress' ? 't1' : null,
    steps: [
      { taskId: 't1', title: 'Export the report', status: first, deps: [] },
      { taskId: 't2', title: 'Check whether Q4 is closed', status: second, deps: ['t1'] },
    ],
  },
});

const finalParts = (SOURCE.frames.find((f) => (f.snapshot as { final?: boolean } | undefined)?.final) as {
  snapshot: { parts: Array<Record<string, unknown>> };
}).snapshot.parts;

function stored(done: boolean) {
  const conversation = buildAguiConversation({
    conversationId: SESSION_ID,
    userMessageId: 'msg-user-ctl3',
    botMessageId: 'msg-bot-ctl3',
    question: QUESTION,
    modelInfo: MODEL_INFO,
    ...(done ? { answer: ANSWER } : {}),
  });
  if (done) Object.assign(conversation.messages[1] as Record<string, unknown>, { parts: finalParts, runId: RUN_ID });
  return conversation;
}

function frames(): SseFrame[] {
  const out: Array<Record<string, unknown>> = [];
  for (const frame of SOURCE.frames) {
    if (frame.type === 'CUSTOM' && frame.name === 'user_input') continue;
    if (frame.type === 'CUSTOM' && frame.name === 'conversation_created') {
      out.push({ ...frame, value: { ...(frame.value as object), conversationId: SESSION_ID } });
      continue;
    }
    if (frame.type === 'RUN_FINISHED') {
      out.push(plan('completed', 'completed'));
      out.push({ ...frame, result: { conversation: stored(true), meta: { requestId: RUN_ID, duration: 1 } } });
      continue;
    }
    out.push(frame);
    if (frame.type === 'RUN_STARTED') out.push(plan('in_progress', 'pending'));
    if (frame.type === 'TOOL_CALL_RESULT') out.push(plan('completed', 'in_progress'));
  }
  return out.map((frame, i) => {
    const { type, ...data } = frame;
    return { id: String(i + 1), type: String(type), data };
  });
}

test.describe('Chat — the answer\'s timeline (CTL-3, needs the e2e stack)', () => {
  test('live steps under the plan, folded into a summary when done; the drawer opens only on click', async ({ page }) => {
    const stream = new FakeRunStream(frames);
    let activityOpened = 0;
    await page.route(`**/runs/${RUN_ID}/activity*`, (route) => {
      activityOpened += 1;
      return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: '' });
    });
    // Through the plan frame that follows the tool's result: step 1 done, step 2 in progress.
    stream.releaseThrough(
      (f) => f.type === 'CUSTOM' && (f.data.value as { currentTaskId?: string } | undefined)?.currentTaskId === 't2',
    );
    await mockInFlightConversation(page, { sessionId: SESSION_ID, runId: RUN_ID, stream, stored: () => stored(stream.finished) });
    await openConversation(page, SESSION_ID);

    const planList = page.getByRole('list', { name: 'Plan' });
    await expect(planList.getByRole('listitem')).toHaveCount(2, { timeout: 15_000 });
    await expect(planList.locator('[aria-current="step"]')).toContainText('Check whether Q4 is closed');
    const header = page.getByRole('button', { name: /Completed 1 of 2 steps · Used Goldendocs: Export report/ });
    await expect(header).toHaveAttribute('aria-expanded', 'true');
    await expect(page.getByRole('button', { name: 'View activity', exact: true })).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Activity' })).toHaveCount(0);

    stream.releaseAll();
    await page.reload();
    await expect(page.getByText(ANSWER)).toBeVisible({ timeout: 15_000 });
    const done = page.getByRole('button', { name: /Used Goldendocs: Export report/ });
    await expect(done).toHaveAttribute('aria-expanded', 'false');
    await done.click();
    await expect(page.getByRole('button', { name: /Goldendocs\s*Export report/ })).toBeVisible();

    const view = page.getByRole('button', { name: 'View activity', exact: true });
    await expect(view).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Activity' })).toHaveCount(0);
    expect(activityOpened).toBe(0);
    await view.click();
    await expect(page.getByRole('complementary', { name: 'Activity' })).toBeVisible();
    expect(activityOpened).toBeGreaterThan(0);
  });
});
