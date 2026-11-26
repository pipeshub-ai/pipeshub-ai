/**
 * chat-background-agents.spec.ts — BGUI-5 (design `jobs-panel.tsx`, shot 04): the chat's Background agents list and
 * its top-bar button in a real browser.
 *
 * A saved chat whose first request handed work to four background agents (two working, one waiting on an ask, one
 * done) and whose second handed out one more. The button shows only because the chat has jobs and carries the count
 * that needs you; the list groups the first request's jobs under what was asked, folds Finished while anything still
 * goes, lists the working agent's tasks from its own stream, opens a request's view, and opens a job through the
 * chat's job panel (`&job=`). Every API is served here (page.route), sign-in included: no stack, no model.
 *
 * With UI_CASE_SHOTS=<dir>, each state saves its pictures there: <state>-<desktop|phone>-<light|dark>.png.
 */
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';

const SESSION_ID = 'conv-bgui5';
const REQUEST = 'run-bgui5-request';
const LATER = 'run-bgui5-later';
const ASK = 'Fix PAY-1423: invoices for EU customers show dollars instead of euros';
const ASK_LATER = 'Write the release note for the fix';
const SHOTS = process.env.UI_CASE_SHOTS ?? '';
const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'quick', modelFriendlyName: 'GPT-4o mini' };

const JOBS = [
  { runId: 'job-fix', goal: 'Fix the currency formatting', engine: 'claude-code', parent: REQUEST, status: 'running' },
  { runId: 'job-logs', goal: 'Check the production logs', engine: 'codex', parent: REQUEST, status: 'running' },
  { runId: 'job-pdf', goal: 'Fix the invoice PDF', engine: 'codex', parent: REQUEST, status: 'running' },
  { runId: 'job-tests', goal: 'Add invoice currency tests', engine: 'claude-code', parent: REQUEST, status: 'completed' },
  { runId: 'job-note', goal: 'Draft the release note', engine: 'codex', parent: LATER, status: 'completed' },
] as const;

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

function fakeToken(): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'none', typ: 'JWT' })}.${part({ userId: 'user-bgui5', orgId: 'org-bgui5', email: 'bgui5@example.com', exp: 4_102_444_800 })}.sig`;
}

const progress = (runId: string, n: number, kind: string, text: string, data: Record<string, unknown> = {}) => ({
  id: `j:${n}`,
  type: 'CUSTOM',
  data: { name: 'engine_progress', runId, value: { tool: 'code_task', taskId: runId, kind, text, data } },
});

const COMMANDS = ['git clone billing', 'npm ci', 'grep -rn formatMoney src', 'npm test -- invoices', 'git diff --stat'];

/** A job's own stream: the steps it took; a working one stays open on its last command, an asking one on its ask. */
function jobFrames(runId: string, status: string): Array<{ id: string; type: string; data: Record<string, unknown> }> {
  const frames: Array<{ id: string; type: string; data: Record<string, unknown> }> = [
    { id: 'j:1', type: 'RUN_STARTED', data: { runId, timestamp: Date.now() - 74_000 } },
    progress(runId, 2, 'started', 'Started', {}),
  ];
  const commands = runId === 'job-logs' ? COMMANDS.slice(0, 2) : runId === 'job-pdf' ? COMMANDS.slice(0, 3) : COMMANDS;
  commands.forEach((command, i) => {
    const callId = `c${i}`;
    frames.push(progress(runId, 10 + i * 2, 'command', command, { callId }));
    if (status !== 'running' || i < commands.length - 1 || runId === 'job-logs')
      frames.push(progress(runId, 11 + i * 2, 'command_done', '', { callId, exitCode: 0, status: 'completed' }));
  });
  if (runId === 'job-logs') {
    frames.push({
      id: 'j:40',
      type: 'CUSTOM',
      data: { name: 'approval_requested', runId, value: { interactionId: 'ix-logs', toolCallId: 'ix-logs', toolName: 'datadog__search_logs', args: '{}' } },
    });
  }
  if (status === 'completed') {
    frames.push(progress(runId, 50, 'finished', 'Done', { success: true }));
    frames.push({ id: 'j:51', type: 'RUN_FINISHED', data: { runId } });
  }
  return frames;
}

const sse = (frames: ReturnType<typeof jobFrames>) =>
  frames.map((f) => `id: ${f.id}\nevent: ${f.type}\ndata: ${JSON.stringify({ type: f.type, ...f.data })}\n\n`).join('');

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
  const parts = (parent: string, answer: string) => [
    { type: 'text', content: answer, runId: parent },
    ...JOBS.filter((j) => j.parent === parent).map((j) => ({
      type: 'sub_agent',
      runId: j.runId,
      parentRunId: parent,
      codeTaskGoal: j.goal,
      codeTaskHandoff: { engine: j.engine },
      status: j.status,
      parts: [],
    })),
  ];
  const first = 'I have handed this to four background agents, each in its own sandbox.';
  const second = 'A background agent is drafting the note.';
  return {
    _id: SESSION_ID,
    id: SESSION_ID,
    title: ASK,
    status: 'complete',
    createdAt: now,
    updatedAt: now,
    access: { isOwner: true, accessLevel: 'OWNER' },
    activeRunId: null,
    messages: [
      message('msg-u1', 'user_query', ASK),
      message('msg-b1', 'bot_response', first, { parts: parts(REQUEST, first), runId: REQUEST, status: 'complete' }),
      message('msg-u2', 'user_query', ASK_LATER),
      message('msg-b2', 'bot_response', second, { parts: parts(LATER, second), runId: LATER, status: 'complete' }),
    ],
  };
}

async function mockBackend(page: Page): Promise<{ requestReads: number }> {
  const seen = { requestReads: 0 };
  const token = fakeToken();
  await page.addInitScript(([access]) => {
    window.localStorage.setItem('jwt_access_token', access);
    window.localStorage.setItem('jwt_refresh_token', access);
  }, [token]);
  await page.route('**/api/**', (route) => json(route, {}));
  await page.route('**/socket.io/**', (route) => route.abort());
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    json(route, { featureFlags: {}, agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true } }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    json(route, { status: 'success', message: 'Success', models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }] }),
  );
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route('**/api/v1/users/user-bgui5', (route) =>
    json(route, { _id: 'user-bgui5', fullName: 'Ada Lovelace', firstName: 'Ada', lastName: 'Lovelace', email: 'bgui5@example.com', hasLoggedIn: true, role: 'admin' }),
  );
  await page.route('**/api/v1/conversations*', (route) =>
    json(route, { conversations: [], source: 'owned', pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false } }),
  );
  const saved = conversation();
  await page.route((url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${SESSION_ID}`, (route) => json(route, { conversation: saved, filters: {}, meta: {} }));
  for (const job of JOBS) {
    const base = `/api/v1/conversations/${SESSION_ID}/runs/${job.runId}`;
    // As Node's projection serves it: the frames after `Last-Event-ID`, so a reconnect never repeats a step.
    await page.route((url) => url.pathname === `${base}/stream`, (route) => {
      const frames = jobFrames(job.runId, job.status);
      const after = route.request().headers()['last-event-id'];
      const from = after ? frames.findIndex((f) => f.id === after) + 1 : 0;
      return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse(frames.slice(from)) });
    });
    await page.route((url) => url.pathname === `${base}/brief`, (route) =>
      json(route, {
        runId: job.runId,
        goal: job.goal,
        sent: { files: [], sources: [], sourcesComplete: true, knowledge: { apps: [], collections: [] }, skills: [], continuesRunId: null },
        withheld: [],
        reach: { internet: 'none', sandbox: true, tools: [], allowedWithoutAsking: 0 },
      }),
    );
    await page.route((url) => url.pathname === `${base}/brief/history`, (route) => json(route, { runId: job.runId, startedAs: job.goal, changes: [] }));
    await page.route((url) => url.pathname === `${base}/timeline`, (route) =>
      json(route, { runId: job.runId, parentRunId: job.parent, status: job.status, parts: [], engine: null, approvals: [], children: [], merge: null, budget: null }),
    );
  }
  await page.route((url) => url.pathname === `/api/v1/conversations/${SESSION_ID}/runs/${REQUEST}/jobs`, (route) => {
    seen.requestReads += 1;
    return json(route, {
      request: { runId: REQUEST, sessionId: SESSION_ID, ask: ASK },
      total: 4,
      back: 1,
      jobs: JOBS.filter((j) => j.parent === REQUEST).map((j, i) => ({
        runId: j.runId,
        title: j.goal,
        engine: j.engine,
        state: j.status === 'completed' ? 'finished' : j.runId === 'job-logs' ? 'needs_you' : 'working',
        status: j.status === 'completed' ? 'completed' : 'running',
        createdAt: Date.now() - 80_000 + i * 1000,
        ...(j.status === 'completed' ? { endedAt: Date.now() - 20_000 } : {}),
      })),
    });
  });
  return seen;
}

const AXE_SOURCE = readFileSync(createRequire(__filename).resolve('axe-core/axe.min.js'), 'utf8');

/** axe (WCAG 2.1 A and AA) over the list and the top-bar button. */
async function axeViolations(page: Page, include: string[]): Promise<unknown[]> {
  if (!(await page.evaluate(() => 'axe' in window))) await page.evaluate(AXE_SOURCE);
  return page.evaluate(async (selectors) => {
    type Result = { violations: Array<{ id: string; help: string; nodes: Array<{ target: string[] }> }> };
    const axe = (window as unknown as { axe: { run: (ctx: unknown, opts: unknown) => Promise<Result> } }).axe;
    const result = await axe.run({ include: selectors.map((s) => [s]) }, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }, resultTypes: ['violations'] });
    return result.violations.map((v) => ({ id: v.id, help: v.help, targets: v.nodes.slice(0, 5).map((n) => n.target.join(' ')) }));
  }, include);
}

async function shoot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity));
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}

const LOOKS = [
  { name: 'desktop-light', viewport: { width: 1440, height: 900 }, dark: false },
  { name: 'desktop-dark', viewport: { width: 1440, height: 900 }, dark: true },
  { name: 'phone-light', viewport: { width: 390, height: 844 }, dark: false },
  { name: 'phone-dark', viewport: { width: 390, height: 844 }, dark: true },
] as const;

async function look(page: Page, l: (typeof LOOKS)[number]): Promise<void> {
  await page.setViewportSize(l.viewport);
  await page.emulateMedia({ colorScheme: l.dark ? 'dark' : 'light' });
  await page.addInitScript((d) => localStorage.setItem('pipeshub-theme-preference', d ? 'dark' : 'light'), l.dark);
}

test.describe('Chat — the Background agents list (BGUI-5)', () => {
  for (const l of LOOKS) {
    test(`the top bar opens this chat's agents, grouped by request; a row opens the job (${l.name})`, async ({ page }) => {
      const phone = l.viewport.width < 500;
      const seen = await mockBackend(page);
      await look(page, l);
      await page.goto(`/chat/?conversationId=${SESSION_ID}`);

      const button = page.getByTestId('background-agents-button');
      await expect(button).toHaveAccessibleName('Background agents, 1 needs you');
      await expect(page.getByTestId('background-agents-count')).toHaveText('1');
      if (phone) {
        const box = await button.boundingBox();
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      }
      await button.click();

      const panel = page.getByTestId('background-agents-panel');
      await expect(panel.getByRole('heading', { name: 'Needs you 1' })).toBeVisible();
      await expect(panel.getByRole('heading', { name: 'Working 2' })).toBeVisible();
      const working = panel.getByTestId('background-agents-section-working');
      await expect(working.getByTestId('background-agents-tasks').first()).toContainText('5 tasks');
      await expect(panel.getByTestId('background-agents-request').first()).toContainText(ASK);
      // Finished folds while anything still goes.
      await expect(panel.getByTestId('background-agents-section-finished').getByTestId('background-agents-row')).toHaveCount(0);
      if (phone) {
        const row = await panel.getByRole('button', { name: 'Fix the currency formatting', exact: true }).boundingBox();
        expect(row?.height ?? 0).toBeGreaterThanOrEqual(44);
      }
      expect(await axeViolations(page, ['[data-testid="background-agents-panel"]', '[data-testid="background-agents-button"]'])).toEqual([]);
      await shoot(page, `01-list-${l.name}`);

      await panel.getByRole('button', { name: /Finished/ }).click();
      await expect(panel.getByTestId('background-agents-section-finished').getByTestId('background-agents-row')).toHaveCount(2);
      await shoot(page, `02-finished-open-${l.name}`);

      await panel.getByTestId('background-agents-request').first().click();
      await expect(panel.getByTestId('background-agents-request-progress')).toHaveText('1 of 4 back');
      expect(seen.requestReads).toBeGreaterThan(0);
      expect(await axeViolations(page, ['[data-testid="background-agents-panel"]'])).toEqual([]);
      await shoot(page, `03-request-${l.name}`);
      if (phone) await panel.locator('xpath=ancestor::*[@role="dialog"][1]').getByRole('button', { name: 'Back', exact: true }).click();
      else await panel.getByRole('button', { name: 'Back to background agents' }).click();

      await page.getByTestId('background-agents-panel').getByRole('button', { name: 'Fix the currency formatting', exact: true }).click();
      await expect(page).toHaveURL(/job=job-fix/);
      await expect(page.getByTestId('background-agents-panel')).toHaveCount(0);
    });
  }

  test('a chat without background jobs has no button', async ({ page }) => {
    await mockBackend(page);
    await page.route((url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${SESSION_ID}`, (route) => {
      const saved = conversation();
      saved.messages = saved.messages.map((m) => ({ ...m, parts: [] }));
      return json(route, { conversation: saved, filters: {}, meta: {} });
    });
    await page.goto(`/chat/?conversationId=${SESSION_ID}`);
    await expect(page.getByText(ASK_LATER).first()).toBeVisible();
    await expect(page.getByTestId('background-agents-button')).toHaveCount(0);
  });
});
