/**
 * CJ-2 (CLASSIC-JOBS D2, D11, D13, §7): the job card in a real browser. The conversation and each job's own stream
 * are served with page.route() (no turn is sent, so no model is reached): a job at work that read the PDF skill, a
 * job that needs you, a done job and one you stopped. The card shows the status line, the now line, the elapsed
 * time, Details and Stop; Stop answers a 409 with "It had already finished"; the skill read shows as FIX-SKILLSHOW labels it.
 *
 * When /work/pw/cj2-shots exists in the lane's run directory, it saves the cards (desktop and phone, light and dark)
 * for the work package's evidence.
 */
import { existsSync } from 'node:fs';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from '../chat/agui-sse-builder';
import { FakeRunStream, json, MODEL_INFO, mockInFlightConversation, openConversation, sse } from '../chat/durable-conversation.mock';

const SHOTS = '/work/pw/cj2-shots';
const SESSION_ID = 'conv-cj2';
const PARENT = 'run-cj2-parent';
const OWNER_PDF_COMMAND =
  "/bin/bash -lc \"cat /pipeshub/AGENTS.md && printf '\\n---PDF SKILL---\\n' && cat /skills/pdf/SKILL.md && cat /pipeshub/tools/INDEX.md\"";

const job = (runId: string, goal: string, patch: Record<string, unknown> = {}) => ({
  type: 'sub_agent',
  runId,
  parentRunId: PARENT,
  roleName: 'pipeshub-subagent',
  codeTaskGoal: goal,
  status: 'running',
  parts: [],
  ...patch,
});

const PARTS = [
  { type: 'text', content: 'I handed the work to four background jobs.', runId: PARENT },
  job('job-working', 'Build the quarterly PDF report'),
  job('job-asking', 'Email the summary to finance'),
  job('job-done', 'Summarise the contracts folder', { status: 'completed', durationMs: 280_000 }),
  job('job-stopped', 'Migrate the CSV export', { status: 'failed', errorType: 'cancelled' }),
];

const progress = (runId: string, kind: string, text: string, data: Record<string, unknown> = {}) => ({
  type: 'CUSTOM',
  name: 'engine_progress',
  runId,
  value: { tool: 'code_task', taskId: runId, kind, text, data },
});

function jobFrames(runId: string): Array<Record<string, unknown>> {
  const started = { type: 'RUN_STARTED', runId, timestamp: Date.now() - 134_000 };
  if (runId === 'job-working')
    return [
      started,
      progress(runId, 'started', 'Build the quarterly PDF report'),
      progress(runId, 'command', OWNER_PDF_COMMAND, { callId: 'c1', skills: ['pdf'] }),
    ];
  if (runId === 'job-asking')
    return [
      started,
      { type: 'CUSTOM', name: 'approval_requested', runId, value: { interactionId: 'ix-1', toolName: 'mail__send_email', displayName: 'Mail · Send email' } },
    ];
  return [];
}

async function mockJobs(page: Page, cancels: string[]): Promise<void> {
  const parent = new FakeRunStream(() => []);
  parent.releaseAll();
  await mockInFlightConversation(page, {
    sessionId: SESSION_ID,
    runId: PARENT,
    stream: parent,
    stored: () => {
      const conversation = buildAguiConversation({
        conversationId: SESSION_ID,
        userMessageId: 'msg-user-cj2',
        botMessageId: 'msg-bot-cj2',
        question: 'Make the quarter-end reports',
        modelInfo: MODEL_INFO,
        answer: 'I handed the work to four background jobs.',
      });
      (conversation.messages[1] as Record<string, unknown>).parts = PARTS;
      return conversation;
    },
  });
  const base = `**/api/v1/conversations/${SESSION_ID}`;
  await page.route(`${base}/runs/job-*/stream`, (route) => {
    const runId = /\/runs\/([^/]+)\/stream/.exec(route.request().url())?.[1] ?? '';
    const frames = jobFrames(runId).map((data, i) => ({ id: `${runId}-${i}`, type: String(data.type), data }));
    const from = route.request().headers()['last-event-id'];
    const shown = from ? frames.slice(frames.findIndex((f) => f.id === from) + 1) : frames;
    return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse(shown) });
  });
  await page.route(`${base}/runs/job-*/timeline`, (route) => {
    const runId = /\/runs\/([^/]+)\/timeline/.exec(route.request().url())?.[1] ?? '';
    return json(route, {
      runId,
      parentRunId: PARENT,
      status: runId === 'job-stopped' ? 'cancelled' : runId === 'job-done' ? 'completed' : 'running',
      parts: [],
      engine: { taskId: runId, frames: jobFrames(runId).filter((f) => f.name === 'engine_progress').map((f) => f.value), truncated: false },
      approvals: [],
      children: [],
      merge: null,
      budget: null,
    });
  });
  await page.route(`${base}/runs/job-*/interactions/authority`, (route) => json(route, { approve: true, deny: true }));
  await page.route(`${base}/runs/job-*/cancel`, (route) => {
    cancels.push(route.request().url());
    return json(route, { error: { code: 'RUN_NOT_CANCELLABLE' } }, 409);
  });
}

const card = (page: Page, title: string) => page.getByTestId('job-card').filter({ hasText: title });

/** The answer's steps, open: a reattach after load can fold them again, so each check opens them if they are not. */
async function ensureOpen(page: Page): Promise<void> {
  const fold = page.getByTestId('activity-summary').first();
  await expect(fold).toBeVisible({ timeout: 30_000 });
  await expect
    .poll(
      async () => {
        if ((await fold.getAttribute('aria-expanded')) !== 'true') await fold.click();
        return page.getByTestId('job-card').count();
      },
      { timeout: 30_000 },
    )
    .toBe(4);
}

async function openJobs(page: Page): Promise<void> {
  await openConversation(page, SESSION_ID);
  await ensureOpen(page);
}

async function shoot(page: Page, name: string): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => document.fonts.check('24px "Material Symbols Outlined"'), null, { timeout: 30_000 }).catch(() => undefined);
  await page.getByTestId('job-card').first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

test.describe('CJ-2: the job card', () => {
  test('status lines, now line, elapsed time, Details, Stop and the skill read', async ({ page }) => {
    if (existsSync(SHOTS)) test.setTimeout(300_000);
    const cancels: string[] = [];
    await mockJobs(page, cancels);
    await openJobs(page);

    const working = card(page, 'Build the quarterly PDF report');
    await expect(working).toHaveAttribute('data-status', 'working');
    await expect(working.getByTestId('job-card-now')).toHaveText('Reading the PDF skill');
    await expect(working.getByTestId('job-card-elapsed')).toHaveText(/^2m \d\ds$/);
    await ensureOpen(page);
    // BGUI-2: the card itself opens the panel, named by its own words (no hidden "Details").
    await expect(working.getByRole('button', { name: /^Build the quarterly PDF report Working/ })).toBeVisible();
    // Four jobs are one board (more than three), a row a job, drawn once and outside the folded steps.
    await expect(page.getByTestId('jobs-board').getByTestId('job-card')).toHaveCount(4);

    const asking = card(page, 'Email the summary to finance');
    await expect(asking).toHaveAttribute('data-status', 'needsYou');
    await expect(asking.getByTestId('job-card-status')).toHaveText('Needs you');
    await expect(asking.getByTestId('job-card-now')).toHaveText('Wants to use Mail · Send email');

    await expect(card(page, 'Summarise the contracts folder').getByTestId('job-card-status')).toHaveText('Done');
    await expect(card(page, 'Summarise the contracts folder').getByTestId('job-card-elapsed')).toHaveText('4m 40s');
    const stopped = card(page, 'Migrate the CSV export');
    await expect(stopped.getByTestId('job-card-status')).toHaveText('Stopped by you');
    await expect(stopped.getByTestId('job-card-stop')).toHaveCount(0);

    await ensureOpen(page);
    // FIX-SKILLSHOW's labelled skill read, in the card's steps; the chained command is behind it.
    await working.getByTestId('sub-agent-toggle').click();
    const command = working.getByTestId('child-run-panel').getByTestId('engine-command');
    await expect(command.getByTestId('engine-command-skill')).toHaveText('Reading the PDF skill');
    await command.getByRole('button').first().click();
    await expect(command.getByTestId('engine-command-line')).toContainText('cat /skills/pdf/SKILL.md');
    if (existsSync(SHOTS)) await shoot(page, '01-cards-steps-desktop-light');
    await working.getByTestId('sub-agent-toggle').click();

    await ensureOpen(page);
    // Stop is one click; a job that had already ended answers 409, in words.
    await working.getByRole('button', { name: 'Stop job: Build the quarterly PDF report' }).click();
    await expect(working.getByTestId('job-card-stop-gone')).toHaveText('It had already finished');
    expect(cancels).toHaveLength(1);

    if (!existsSync(SHOTS)) return;
    for (const [name, viewport, dark] of [
      ['desktop-light', { width: 1440, height: 960 }, false],
      ['desktop-dark', { width: 1440, height: 960 }, true],
      ['phone-light', { width: 390, height: 844 }, false],
      ['phone-dark', { width: 390, height: 844 }, true],
    ] as const) {
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.evaluate((d) => localStorage.setItem('pipeshub-theme-preference', d ? 'dark' : 'light'), dark);
      await openJobs(page);
      await expect(card(page, 'Build the quarterly PDF report').getByTestId('job-card-now')).toHaveText('Reading the PDF skill');
      await shoot(page, `02-cards-${name}`);
    }
  });
});
