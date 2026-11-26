/**
 * BGUI-2: the design's job card in the classic chat, in a real browser. The conversation and each job's own stream
 * are served with page.route() (no turn is sent, so no model is reached). Two turns: one that handed out three jobs
 * (a card each, after the steps and outside their fold, the hand-off said in one line before them) and one that
 * handed out five (one board, a row a job, that folds). Each job is drawn once; the cards' touch targets hold on a
 * phone; the page has no axe violation in the cards.
 *
 * With `E2E_UI_CASE_SHOTS=/work/pw/<dir>` on run.sh it saves the cards and the board, light, dark and phone.
 */
import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from '../chat/agui-sse-builder';
import { FakeRunStream, json, MODEL_INFO, mockInFlightConversation, openConversation, sse } from '../chat/durable-conversation.mock';
import { expectNoAxeViolations } from './support/classic-jobs';

const PARENT = 'run-bgui2-parent';
const CLAUDE = { trigger: 'upfront', reason: 'many_steps', engine: 'claude_code' };

const job = (runId: string, goal: string, patch: Record<string, unknown> = {}) => ({
  type: 'sub_agent',
  runId,
  parentRunId: PARENT,
  roleName: 'pipeshub-subagent',
  codeTaskGoal: goal,
  codeTaskHandoff: CLAUDE,
  status: 'running',
  parts: [],
  ...patch,
});

const CODEX = { ...CLAUDE, engine: 'codex' };

const TURNS = {
  cards: {
    sessionId: 'conv-bgui2-cards',
    question: 'Fix PAY-1423: invoices for EU customers show dollars',
    answer: 'I handed the fix to background agents in their own sandboxes.',
    jobs: [
      job('job-working', 'Fix PAY-1423'),
      job('job-asking', 'Open the pull request', { codeTaskHandoff: CODEX }),
      job('job-done', 'Write the regression test', { status: 'completed', durationMs: 14_000 }),
    ],
  },
  board: {
    sessionId: 'conv-bgui2-board',
    question: 'Build SSO login from the spec in Confluence',
    answer: "I've split this across five background agents.",
    jobs: [
      job('job-working', 'Build SSO endpoints'),
      job('job-asking', 'Open pull request', { codeTaskHandoff: CODEX }),
      job('job-done', 'Build login page', { status: 'completed', durationMs: 13_000 }),
      job('job-stopped', 'Build admin settings', { status: 'failed', errorType: 'cancelled', codeTaskHandoff: CODEX }),
      job('job-failed', 'Add SSO tables', { status: 'failed', errorType: 'engine_crashed' }),
    ],
  },
} as const;

type Turn = (typeof TURNS)[keyof typeof TURNS];

const progress = (runId: string, kind: string, text: string, data: Record<string, unknown> = {}) => ({
  type: 'CUSTOM',
  name: 'engine_progress',
  runId,
  value: { tool: 'code_task', taskId: runId, kind, text, data },
});

function jobFrames(runId: string): Array<Record<string, unknown>> {
  const started = { type: 'RUN_STARTED', runId, timestamp: Date.now() - 4_000 };
  if (runId === 'job-working') return [started, progress(runId, 'started', ''), progress(runId, 'command', 'npm test -- invoices', { callId: 'c1' })];
  if (runId === 'job-asking')
    return [started, { type: 'CUSTOM', name: 'approval_requested', runId, value: { interactionId: 'ix-1', toolName: 'github__create_pull_request', displayName: 'GitHub · Open pull request' } }];
  return [];
}

async function mockTurn(page: Page, turn: Turn): Promise<void> {
  const parent = new FakeRunStream(() => []);
  parent.releaseAll();
  await mockInFlightConversation(page, {
    sessionId: turn.sessionId,
    runId: PARENT,
    stream: parent,
    stored: () => {
      const conversation = buildAguiConversation({
        conversationId: turn.sessionId,
        userMessageId: `${turn.sessionId}-user`,
        botMessageId: `${turn.sessionId}-bot`,
        question: turn.question,
        modelInfo: MODEL_INFO,
        answer: turn.answer,
      });
      (conversation.messages[1] as Record<string, unknown>).parts = [
        { type: 'reasoning', content: 'This needs the code changed and tested.', runId: PARENT },
        { type: 'text', content: 'Handing it to background agents.', runId: PARENT },
        ...turn.jobs,
      ];
      return conversation;
    },
  });
  const base = `**/api/v1/conversations/${turn.sessionId}`;
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
      status: 'running',
      parts: [],
      engine: { taskId: runId, frames: jobFrames(runId).filter((f) => f.name === 'engine_progress').map((f) => f.value), truncated: false },
      approvals: [],
      children: [],
      merge: null,
      budget: null,
    });
  });
  await page.route(`${base}/runs/job-*/interactions/authority`, (route) => json(route, { approve: true, deny: true }));
}

const LOOKS = [
  ['desktop-light', { width: 1440, height: 900 }, false],
  ['desktop-dark', { width: 1440, height: 900 }, true],
  ['phone-light', { width: 390, height: 844 }, false],
  ['phone-dark', { width: 390, height: 844 }, true],
] as const;

async function look(page: Page, viewport: { width: number; height: number }, dark: boolean): Promise<void> {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
  await page.addInitScript((d) => localStorage.setItem('pipeshub-theme-preference', d ? 'dark' : 'light'), dark);
}

async function shoot(page: Page, name: string): Promise<void> {
  const dir = process.env.UI_CASE_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => document.fonts.check('24px "Material Icons Outlined"'), null, { timeout: 15_000 }).catch(() => undefined);
  await page.getByTestId('turn-jobs').evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await page.screenshot({ path: path.join(dir, `bgui2-${name}.png`) });
}

test.describe('BGUI-2: the job card', () => {
  for (const [name, viewport, dark] of LOOKS) {
    test(`three cards after the steps, drawn once (${name})`, async ({ page }) => {
      const turn = TURNS.cards;
      await look(page, viewport, dark);
      await mockTurn(page, turn);
      await openConversation(page, turn.sessionId);
      const jobs = page.getByTestId('turn-jobs');
      await expect(jobs.getByTestId('job-card')).toHaveCount(3, { timeout: 30_000 });
      // Drawn once: none in the steps, even opened.
      const fold = page.getByTestId('activity-summary').first();
      if ((await fold.getAttribute('aria-expanded')) !== 'true') await fold.click();
      await expect(page.getByTestId('job-card')).toHaveCount(3);
      if ((await fold.getAttribute('aria-expanded')) === 'true') await fold.click();
      // One line a distinct hand-off, in hand-out order.
      await expect(jobs.getByTestId('job-handoff-line')).toHaveText(['Handed to Claude Code: this needs many steps', 'Handed to Codex: this needs many steps']);
      const working = jobs.locator('[data-run-id="job-working"]');
      await expect(working).toHaveAttribute('data-status', 'working');
      await expect(working.getByTestId('job-card-now')).toHaveText('Running npm test -- invoices');
      await expect(working.getByTestId('job-card-details')).toContainText(/Working · \d+s · Claude Code/);
      await expect(jobs.locator('[data-run-id="job-asking"]')).toHaveAttribute('data-status', 'needsYou');
      await expect(jobs.locator('[data-run-id="job-done"]').getByTestId('job-card-status')).toHaveText('Done');
      if (viewport.width < 500) {
        for (const target of [working.getByTestId('job-card-stop'), working.getByTestId('sub-agent-toggle')]) {
          const box = await target.boundingBox();
          expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
        }
      }
      await expectNoAxeViolations(page, `the job cards, ${name}`, ['[data-testid="turn-jobs"]']);
      await shoot(page, `cards-${name}`);
      if (name === 'desktop-light') {
        await working.getByTestId('sub-agent-toggle').click();
        await expect(working.getByTestId('child-run-panel')).toBeVisible();
        await shoot(page, 'cards-steps-open-desktop-light');
      }
    });

    test(`five jobs are one board (${name})`, async ({ page }) => {
      const turn = TURNS.board;
      await look(page, viewport, dark);
      await mockTurn(page, turn);
      await openConversation(page, turn.sessionId);
      const board = page.getByTestId('jobs-board');
      await expect(board.getByTestId('job-card')).toHaveCount(5, { timeout: 30_000 });
      await expect(board.getByTestId('jobs-board-count')).toHaveText('5 agents');
      await expect(page.getByTestId('job-card')).toHaveCount(5);
      await expect(board.locator('[data-run-id="job-failed"]').getByTestId('job-card-status')).toHaveText("Couldn't finish");
      await expect(board.locator('[data-run-id="job-stopped"]').getByTestId('job-card-status')).toHaveText('Stopped by you');
      await expectNoAxeViolations(page, `the jobs board, ${name}`, ['[data-testid="turn-jobs"]']);
      await shoot(page, `board-${name}`);
      if (name === 'desktop-light') {
        await page.getByRole('button', { name: 'Hide agents' }).click();
        await expect(board.getByTestId('job-card')).toHaveCount(0);
        await shoot(page, 'board-folded-desktop-light');
      }
    });
  }
});
