/**
 * chat-activity-panel.spec.ts — UX5 FE-9: the activity panel and the
 * suggested next steps in a real browser. A saved engine-run answer (its
 * `engine_task` part names the run, as U3 saves it) is opened; the panel's
 * `GET …/runs/:runId/activity` is served as `text/event-stream` in the shape
 * of UX5 §3.4 (snapshot, command, output, end), and the run's follow-ups
 * come from `GET …/runs/:runId/followups` (stored after the run ended; a
 * reloaded answer asks with `waitMs=0`). Session routes are mocked with page.route(); the `setup`
 * project signs in against the e2e stack, like chat-durable-run-ui.spec.ts.
 */
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';

const SESSION_ID = 'conv-ux5-activity';
const RUN_ID = 'run-ux5-activity';
const QUESTION = 'Fix the rounding bug in the invoice totals';
const ANSWER = 'The totals now round half up; the invoice tests pass.';
const FOLLOWUPS = ['Which invoices were affected?', 'Add a test for negative totals'];
const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'quick', modelFriendlyName: 'GPT-4o mini' };

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

const sse = (events: Array<[string, unknown]>) =>
  events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('');

const COMMAND = {
  commandId: 'inv-1',
  source: 'engine',
  command: 'pytest -q tests/test_invoice.py',
  status: 'running',
  exitCode: null,
  startedAt: 1_760_000_000_000,
  durationMs: null,
  stdout: '',
  stderr: '',
  truncated: false,
  liveDropped: 0,
  hasLog: false,
};

function activityView(status: string, commands: unknown[]) {
  return {
    runId: RUN_ID,
    status,
    engine: true,
    commands,
    omittedCommands: 0,
    tools: [],
    tasks: [{ taskId: 'task-1', title: 'Run the invoice tests', status: status === 'completed' ? 'completed' : 'in_progress' }],
    files: [{ path: 'billing/invoice.py', change: 'modified', diff: '--- a/billing/invoice.py\n+++ b/billing/invoice.py\n@@ -1 +1 @@\n-ROUND_DOWN\n+ROUND_HALF_UP\n' }],
    followups: [],
  };
}

function savedConversation() {
  const conversation = buildAguiConversation({
    conversationId: SESSION_ID,
    userMessageId: 'msg-user-ux5',
    botMessageId: 'msg-bot-ux5',
    question: QUESTION,
    answer: ANSWER,
    modelInfo: MODEL_INFO,
  });
  (conversation.messages[1] as Record<string, unknown>).parts = [
    {
      type: 'engine_task',
      taskId: RUN_ID,
      runId: RUN_ID,
      frames: [
        { tool: 'code_task', taskId: RUN_ID, kind: 'started', text: QUESTION, data: {} },
        { tool: 'code_task', taskId: RUN_ID, kind: 'command', text: COMMAND.command, data: {} },
        { tool: 'code_task', taskId: RUN_ID, kind: 'finished', text: ANSWER, data: { success: true } },
      ],
    },
    { type: 'text', content: ANSWER, isFinalAnswer: true },
  ];
  return conversation;
}

interface Traffic {
  sent: string[];
  activityOpened: number;
  followupsWaits: string[];
}

async function mockBackend(page: Page): Promise<Traffic> {
  const traffic: Traffic = { sent: [], activityOpened: 0, followupsWaits: [] };
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    json(route, {
      featureFlags: {},
      agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true },
    }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    json(route, {
      status: 'success',
      message: 'Success',
      models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }],
    }),
  );
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route('**/api/v1/conversations*', (route) =>
    route.request().method() === 'GET'
      ? json(route, {
          conversations: [],
          source: 'owned',
          pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
        })
      : route.fallback(),
  );
  await page.route('**/api/v1/conversations/**', (route) => {
    if (route.request().method() === 'POST') traffic.sent.push(new URL(route.request().url()).pathname);
    return route.fallback();
  });
  const base = `**/api/v1/conversations/${SESSION_ID}`;
  await page.route(`${base}/runs/${RUN_ID}/timeline`, (route) =>
    json(route, {
      runId: RUN_ID,
      parentRunId: null,
      status: 'completed',
      parts: [],
      engine: null,
      approvals: [],
      children: [],
      merge: null,
      budget: null,
      followups: [],
    }),
  );
  await page.route(
    (url) => url.pathname === `/api/v1/conversations/${SESSION_ID}/runs/${RUN_ID}/followups`,
    (route) => {
      traffic.followupsWaits.push(new URL(route.request().url()).searchParams.get('waitMs') ?? '');
      return json(route, { followups: FOLLOWUPS.map((text) => ({ text })) });
    },
  );
  await page.route(`${base}/runs/${RUN_ID}/activity`, (route) => {
    traffic.activityOpened += 1;
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: sse([
        ['snapshot', activityView('running', [COMMAND])],
        ['output', { commandId: COMMAND.commandId, stream: 'stdout', text: '..F\n' }],
        ['output', { commandId: COMMAND.commandId, stream: 'stdout', text: '3 passed in 0.42s\n' }],
        ['command', { ...COMMAND, status: 'completed', exitCode: 0, durationMs: 1_250 }],
        ['snapshot', activityView('completed', [{ ...COMMAND, status: 'completed', exitCode: 0, durationMs: 1_250, stdout: '..F\n3 passed in 0.42s\n' }])],
        ['end', { status: 'completed' }],
      ]),
    });
  });
  await page.route(`${base}/runs/${RUN_ID}/interactions/authority`, (route) => json(route, { approve: true, deny: true }));
  await page.route(`${base}/workspace/versions`, (route) => json(route, { workspaceId: `ws:${SESSION_ID}`, head: 0, versions: [] }));
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${SESSION_ID}`,
    (route) =>
      route.request().method() !== 'GET'
        ? route.fallback()
        : json(route, {
            conversation: { ...savedConversation(), id: SESSION_ID, access: { isOwner: true, accessLevel: 'OWNER' }, activeRunId: null },
            filters: {},
            meta: {},
          }),
  );
  return traffic;
}

test.describe('Chat — the activity panel and suggested next steps (UX5, needs the e2e stack)', () => {
  test('FE-9: open the panel, see a command finish; a chip fills the composer and sends nothing', async ({ page }) => {
    const traffic = await mockBackend(page);
    await page.goto(`/chat/?conversationId=${SESSION_ID}`);

    // CTL-3: the drawer opens from the answer's own "View activity", never by itself.
    const toggle = page.getByRole('button', { name: 'View activity', exact: true });
    await expect(toggle).toBeVisible({ timeout: 15_000 });
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(traffic.activityOpened).toBe(0);

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const panel = page.getByRole('complementary', { name: 'Activity' });
    await expect(panel).toBeVisible();
    const block = panel.getByTestId('activity-command');
    await expect(block.getByText(COMMAND.command)).toBeVisible();
    await expect(panel.getByRole('group', { name: `Output of ${COMMAND.command}` })).toContainText('3 passed in 0.42s');
    await expect(block.getByText('Exit 0')).toBeVisible();
    await expect(panel.getByText('Finished', { exact: true })).toBeVisible();
    await expect(panel.locator('input, textarea')).toHaveCount(0);

    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(toggle).toBeFocused();

    const chip = page.getByRole('button', { name: FOLLOWUPS[1] as string });
    await expect(chip).toBeVisible();
    await chip.click();
    const composer = page.locator('textarea').last();
    await expect(composer).toHaveValue(FOLLOWUPS[1] as string);
    await expect(composer).toBeFocused();
    expect(traffic.sent).toEqual([]);
    expect(traffic.followupsWaits.length).toBeGreaterThan(0);
    expect(new Set(traffic.followupsWaits), 'a reloaded answer does not wait').toEqual(new Set(['0']));
  });
});
