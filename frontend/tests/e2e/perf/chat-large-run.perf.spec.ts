/**
 * chat-large-run.perf.spec.ts — EV-15 and PF-17 in a real browser: a saved answer whose run took 10,000 steps, and
 * its activity panel with 10,000 tool steps, 200 commands and 10 MB of terminal output streaming in.
 *
 * - EV-15: the conversation is usable within 2 s of navigation (its steps' summary is on screen), the timeline keeps
 *   a bounded DOM, and scrolling to the middle shows the rows there.
 * - PF-17: while the panel takes the 10 MB, typing in the composer stays interactive: every keystroke's input
 *   latency (event to the next frame, and the browser's own Event Timing) is under 100 ms.
 *
 * Every API is served here (page.route), sign-in included, so the spec needs only the built app: no stack, no
 * model, nothing spent.
 */
import type { Page, Route } from '@playwright/test';
import { test, expect } from '@playwright/test';

const SESSION_ID = 'conv-pf17-large';
const RUN_ID = 'run-pf17-large';
const QUESTION = 'Refactor the billing module and run every test';
const ANSWER = 'The billing module is refactored; all 4,812 tests pass.';
const STEPS = 10_000;
const PANEL_TOOLS = 10_000;
const COMMANDS = 200;
const TERMINAL_BYTES = 10 * 1024 * 1024;
const CHUNK_BYTES = 4 * 1024;
const LOAD_BUDGET_MS = 2_000;
const INPUT_BUDGET_MS = 100;
const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'quick', modelFriendlyName: 'GPT-4o mini' };

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

/** An unsigned token the page reads as signed in until 2100; every API that would check it is served here. */
function fakeToken(): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'none', typ: 'JWT' })}.${part({
    userId: 'user-pf17',
    orgId: 'org-pf17',
    email: 'perf@example.com',
    exp: 4_102_444_800,
  })}.sig`;
}

function savedParts(): Array<Record<string, unknown>> {
  const steps: Array<Record<string, unknown>> = [];
  for (let i = 0; i < STEPS; i += 1) {
    steps.push(
      i % 2
        ? {
            type: 'tool_call',
            toolCallId: `call-${i}`,
            toolName: i % 4 === 1 ? 'jira__search_issues' : 'knowledgehub__search',
            args: JSON.stringify({ query: `step ${i}` }),
            status: 'completed',
            runId: RUN_ID,
            argsSummary: `Search: "step ${i}"`,
            resultSummary: 'Completed successfully',
          }
        : { type: 'text', content: `Checked part ${i} of the billing module.`, runId: RUN_ID },
    );
  }
  return [
    {
      type: 'engine_task',
      taskId: RUN_ID,
      runId: RUN_ID,
      frames: [
        { tool: 'code_task', taskId: RUN_ID, kind: 'started', text: QUESTION, data: {} },
        { tool: 'code_task', taskId: RUN_ID, kind: 'finished', text: ANSWER, data: { success: true } },
      ],
    },
    ...steps,
    { type: 'text', content: ANSWER, runId: RUN_ID, isFinal: true },
  ];
}

function conversation() {
  const now = new Date().toISOString();
  const message = (id: string, messageType: string, content: string, extra: Record<string, unknown> = {}) => ({
    _id: id,
    messageType,
    content,
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    referenceData: [],
    modelInfo: MODEL_INFO,
    createdAt: now,
    updatedAt: now,
    feedback: [],
    ...extra,
  });
  return {
    _id: SESSION_ID,
    id: SESSION_ID,
    title: QUESTION,
    status: 'complete',
    createdAt: now,
    updatedAt: now,
    access: { isOwner: true, accessLevel: 'OWNER' },
    activeRunId: null,
    messages: [
      message('msg-user-pf17', 'user_query', QUESTION),
      message('msg-bot-pf17', 'bot_response', ANSWER, { parts: savedParts(), runId: RUN_ID, status: 'complete' }),
    ],
  };
}

const command = (i: number, status: string) => ({
  commandId: `cmd-${i}`,
  source: 'engine',
  command: `pytest -q tests/billing/test_part_${i}.py`,
  status,
  exitCode: status === 'running' ? null : 0,
  startedAt: 1_760_000_000_000 + i,
  durationMs: status === 'running' ? null : 900,
  stdout: '',
  stderr: '',
  truncated: false,
  liveDropped: 0,
  hasLog: false,
});

function activityView(status: string) {
  return {
    runId: RUN_ID,
    status,
    engine: true,
    commands: Array.from({ length: COMMANDS }, (_, i) => command(i, i === COMMANDS - 1 ? 'running' : 'completed')),
    omittedCommands: 0,
    tools: Array.from({ length: PANEL_TOOLS }, (_, i) => ({
      toolCallId: `panel-${i}`,
      name: i % 3 ? 'jira__search_issues' : 'knowledgehub__search',
      displayName: null,
      status: 'completed',
      durationMs: 120,
    })),
    tasks: [],
    files: [],
    followups: [],
  };
}

const sseEvent = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** The snapshot, then 10 MB of the last command's output in 4 KiB events, then its end. */
function activityStream(): string {
  const line = `${'.'.repeat(78)}\n`;
  const chunk = line.repeat(Math.floor(CHUNK_BYTES / line.length));
  const parts = [sseEvent('snapshot', activityView('running'))];
  for (let sent = 0, n = 0; sent < TERMINAL_BYTES; sent += chunk.length, n += 1) {
    parts.push(sseEvent('output', { commandId: `cmd-${COMMANDS - 1}`, stream: 'stdout', text: n % 64 === 63 ? `${chunk}batch ${n} passed\n` : chunk }));
  }
  parts.push(sseEvent('output', { commandId: `cmd-${COMMANDS - 1}`, stream: 'stdout', text: 'STREAM-DONE 4812 passed\n' }));
  parts.push(sseEvent('command', { ...command(COMMANDS - 1, 'completed') }));
  parts.push(sseEvent('end', { status: 'completed' }));
  return parts.join('');
}

async function mockBackend(page: Page): Promise<void> {
  const token = fakeToken();
  await page.addInitScript(
    ([access]) => {
      window.localStorage.setItem('jwt_access_token', access);
      window.localStorage.setItem('jwt_refresh_token', access);
    },
    [token],
  );
  // Registered first, so every route below takes precedence; an API the page reads and nothing here serves is empty.
  await page.route('**/api/**', (route) => json(route, {}));
  await page.route('**/socket.io/**', (route) => route.abort());
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    json(route, { featureFlags: {}, agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true } }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    json(route, {
      status: 'success',
      message: 'Success',
      models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }],
    }),
  );
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route('**/api/v1/users/user-pf17', (route) =>
    json(route, { _id: 'user-pf17', fullName: 'Perf Tester', firstName: 'Perf', lastName: 'Tester', email: 'perf@example.com', hasLoggedIn: true, role: 'admin' }),
  );
  await page.route('**/api/v1/conversations*', (route) =>
    json(route, {
      conversations: [],
      source: 'owned',
      pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
    }),
  );
  const saved = conversation();
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${SESSION_ID}`,
    (route) => json(route, { conversation: saved, filters: {}, meta: {} }),
  );
  const base = `**/api/v1/conversations/${SESSION_ID}`;
  await page.route(`${base}/runs/${RUN_ID}/timeline`, (route) =>
    json(route, { runId: RUN_ID, parentRunId: null, status: 'completed', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null, followups: [] }),
  );
  await page.route(`${base}/runs/${RUN_ID}/followups*`, (route) => json(route, { followups: [] }));
  await page.route(`${base}/workspace/versions`, (route) => json(route, { workspaceId: `ws:${SESSION_ID}`, head: 0, versions: [] }));
  const body = activityStream();
  await page.route(`${base}/runs/${RUN_ID}/activity`, (route) =>
    route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' }, body }),
  );
}

interface Latencies {
  /** Per keystroke: from the event's timestamp to the frame after its handlers ran. */
  frames: number[];
  /** The browser's Event Timing entries for keyboard and input events (only those of 16 ms or more are reported). */
  timings: Array<{ name: string; duration: number }>;
  /** Main-thread tasks of 50 ms or more from when recording began (for the report; the budget is on input). */
  longTasks: number[];
}

/** Starts recording every keystroke's latency in the page. */
async function recordInputLatency(page: Page): Promise<void> {
  await page.evaluate(() => {
    const store = { frames: [] as number[], timings: [] as Array<{ name: string; duration: number }>, longTasks: [] as number[] };
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) store.longTasks.push(entry.duration);
    }).observe({ type: 'longtask' });
    (window as unknown as { __latency: typeof store }).__latency = store;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (/^key|^input|^beforeinput/.test(entry.name)) store.timings.push({ name: entry.name, duration: entry.duration });
      }
    }).observe({ type: 'event', buffered: true, durationThreshold: 16 } as PerformanceObserverInit);
    document.addEventListener(
      'keydown',
      (event) => {
        const stamp = event.timeStamp;
        requestAnimationFrame(() => setTimeout(() => store.frames.push(performance.now() - stamp), 0));
      },
      true,
    );
  });
}

const readLatency = (page: Page) => page.evaluate(() => (window as unknown as { __latency: Latencies }).__latency);

test.describe('EV-15 / PF-17: a 10,000-step run and 10 MB of terminal output stay responsive', () => {
  test('EV-15: the conversation is usable in under 2 s, keeps a bounded DOM and scrolls to any step', async ({ page }) => {
    await mockBackend(page);
    await page.goto(`/chat/?conversationId=${SESSION_ID}`);
    const summary = page.getByTestId('activity-summary').first();
    await expect(summary).toBeVisible({ timeout: 30_000 });
    const loadedMs = await page.evaluate(() => performance.now());
    test.info().annotations.push({ type: 'EV-15 load ms', description: loadedMs.toFixed(0) });
    expect(loadedMs, 'from navigation to the steps being on screen').toBeLessThan(LOAD_BUDGET_MS);

    await summary.click();
    const rows = page.getByTestId('agent-activity-rows');
    await expect(rows).toHaveAttribute('data-virtualized', 'true');
    await expect(rows.getByText('Checked part 0 of the billing module.')).toBeVisible();
    expect(await rows.locator('[data-vrow]').count()).toBeLessThan(200);

    // The page's own scroll container. The steps open with a 300 ms animation: wait until it holds the whole list.
    await rows.evaluate((list) => {
      let scroller: HTMLElement | null = list.parentElement;
      while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)) scroller = scroller.parentElement;
      (scroller ?? (document.scrollingElement as HTMLElement)).dataset.perfScroller = 'true';
    });
    const scroller = page.locator('[data-perf-scroller="true"]');
    await expect.poll(() => scroller.evaluate((el) => el.scrollHeight)).toBeGreaterThan(200_000);
    await scroller.evaluate((target) => {
      const list = document.querySelector('[data-testid="agent-activity-rows"]') as HTMLElement;
      const listTop = list.getBoundingClientRect().top - target.getBoundingClientRect().top + target.scrollTop;
      target.scrollTo({ top: listTop + list.getBoundingClientRect().height / 2, behavior: 'instant' });
    });
    await expect.poll(() => scroller.evaluate((el) => el.scrollTop)).toBeGreaterThan(100_000);
    await expect
      .poll(async () => rows.locator('[data-vrow]').evaluateAll((els) => els.map((el) => el.textContent ?? '').join(' ')))
      .toMatch(/Checked part (4|5)\d{3} of the billing module/);
    expect(await rows.locator('[data-vrow]').count()).toBeLessThan(200);
  });

  test('PF-17: typing stays under 100 ms per keystroke while the panel takes 10 MB of output', async ({ page }) => {
    await mockBackend(page);
    await page.goto(`/chat/?conversationId=${SESSION_ID}`);
    const toggle = page.getByRole('button', { name: 'View activity', exact: true });
    await expect(toggle).toBeVisible({ timeout: 30_000 });
    const composer = page.locator('textarea.chat-input-textarea');
    await expect(composer).toBeVisible();
    await composer.click();
    await recordInputLatency(page);

    await toggle.click();
    const panel = page.getByRole('complementary', { name: 'Activity' });
    await composer.click();
    const started = Date.now();
    // Typing from the moment the panel opens, through its snapshot and the stream: 60 keystrokes, a person's pace.
    await composer.pressSequentially('Run the slow billing tests again with verbose output, please!', { delay: 40 });
    await expect(composer).toHaveValue('Run the slow billing tests again with verbose output, please!');
    await expect(panel.getByText('STREAM-DONE 4812 passed')).toBeVisible({ timeout: 60_000 });
    test.info().annotations.push({ type: 'PF-17 stream folded within ms of typing start', description: String(Date.now() - started) });

    const latency = await readLatency(page);
    const worst = Math.max(0, ...latency.frames);
    const sorted = [...latency.frames].sort((a, b) => a - b);
    test.info().annotations.push({
      type: 'PF-17 input latency ms',
      description: `longTasks=${JSON.stringify(latency.longTasks.map(Math.round))} n=${sorted.length} p50=${sorted[Math.floor(sorted.length / 2)]?.toFixed(1)} max=${worst.toFixed(1)} eventTimingMax=${Math.max(0, ...latency.timings.map((entry) => entry.duration))}`,
    });
    expect(latency.frames.length).toBeGreaterThanOrEqual(60);
    expect(worst, 'a keystroke to its next frame').toBeLessThan(INPUT_BUDGET_MS);
    for (const entry of latency.timings) expect(entry.duration, `Event Timing ${entry.name}`).toBeLessThan(INPUT_BUDGET_MS);

    // The DOM stayed bounded with all of it on screen.
    await expect(panel.getByTestId('activity-steps')).toHaveAttribute('data-virtualized', 'true');
    expect(await panel.getByTestId('activity-command').count()).toBeLessThan(30);
    expect(await panel.getByTestId('activity-steps').locator('[data-vrow]').count()).toBeLessThan(200);
  });
});
