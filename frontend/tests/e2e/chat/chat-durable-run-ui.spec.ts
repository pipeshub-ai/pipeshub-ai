/**
 * chat-durable-run-ui.spec.ts — U3 PW-1..PW-4: a durable engine run in a real
 * browser. The run's frames and its saved parts are Node's own: the projector
 * and the transcript over the U3 journals (`contracts/harness/vectors/journals/
 * u3_*.json`, kept by `backend/nodejs/apps/tests/contracts/durable-engine-ui.test.ts`).
 * The session's routes are mocked with page.route() as a conversation the page
 * opens while its run is going (`durable-conversation.mock.ts`); the `setup`
 * project signs in against the harness lane's stack (`deployment/e2e/run.sh
 * harness`). The real engine run is `chat-durable-engine-e2e.spec.ts` (nightly).
 * CTL-4: the engine run is on the answer's timeline (its commands as steps, its files under "Files changed"), and
 * its cost and workspace versions are in "View activity"; there is no code-task card.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';
import { FakeRunStream, json, MODEL_INFO, mockInFlightConversation, openConversation } from './durable-conversation.mock';

const JOURNALS = join(__dirname, '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals');
const vector = <T>(name: string): T => JSON.parse(readFileSync(join(JOURNALS, name), 'utf8')) as T;

interface ProjectedFrame {
  id: string;
  frame: Record<string, unknown>;
}

const ENGINE = vector<{ runId: string; sessionId: string; query: string }>('u3_engine_run.journal.json');
const ENGINE_FRAMES = vector<{ frames: ProjectedFrame[] }>('u3_engine_run.frames.json').frames;
const ENGINE_SAVED = vector<{ answer: string; parts: Array<Record<string, unknown>> }>('u3_engine_run.parts.json');
const PARENT = vector<{ runId: string; query: string }>('u3_parent_with_engine_child.journal.json');
const PARENT_SAVED = vector<{ answer: string; parts: Array<Record<string, unknown>> }>('u3_parent_with_engine_child.parts.json');

const SESSION_ID = ENGINE.sessionId;
const RUN_ID = ENGINE.runId;
const BUDGET = {
  cap: { usdMicros: 5_000_000, tokens: 1_000_000 },
  committed: { usdMicros: 420_000, tokens: 1_280 },
  reserved: { usdMicros: 0, tokens: 0 },
  available: { usdMicros: 4_580_000, tokens: 998_720 },
  exhausted: false,
};

/** The conversation as Node saves it: the answer and the transcript's parts. */
function stored(query: string, saved: { answer: string; parts: Array<Record<string, unknown>> } | null) {
  const conversation = buildAguiConversation({
    conversationId: SESSION_ID,
    userMessageId: 'msg-user-u3',
    botMessageId: 'msg-bot-u3',
    question: query,
    modelInfo: MODEL_INFO,
    ...(saved !== null ? { answer: saved.answer } : {}),
  });
  if (saved !== null) {
    (conversation.messages[1] as Record<string, unknown>).parts = saved.parts;
  }
  return conversation;
}

/** The run's frames as Node projects them (`durable-conversation.mock.ts` serves them after `Last-Event-ID`). */
class EngineRunServer {
  decisions: unknown[] = [];
  restores: unknown[] = [];
  head = 2;
  readonly stream: FakeRunStream;

  constructor(frames: ProjectedFrame[]) {
    this.stream = new FakeRunStream(() =>
      frames.map(({ id, frame }) => {
        const { type, ...data } =
          frame.type === 'RUN_FINISHED'
            ? { ...frame, result: { conversation: stored(ENGINE.query, ENGINE_SAVED), meta: { requestId: RUN_ID, duration: 1 } } }
            : frame;
        return { id, type: String(type), data };
      }),
    );
  }

  get finished(): boolean {
    return this.stream.finished;
  }

  versions(): Record<string, unknown> {
    return {
      workspaceId: `ws:${SESSION_ID}`,
      head: this.head,
      versions: Array.from({ length: this.head }, (_, i) => this.head - i).map((revision) => ({
        revision,
        parentRevision: revision > 1 ? revision - 1 : null,
        reason: revision === 3 ? 'rollback' : revision === 2 ? 'turn_end' : 'autosave',
        createdAt: revision,
        fileCount: 1,
        contentHash: revision === 3 ? 'h1' : `h${revision}`,
        restoredByUser: revision === 3,
      })),
    };
  }
}

async function mockBackend(page: Page, server: EngineRunServer, saved: () => ReturnType<typeof stored>): Promise<void> {
  await mockInFlightConversation(page, { sessionId: SESSION_ID, runId: RUN_ID, stream: server.stream, stored: saved });
  const base = `**/api/v1/conversations/${SESSION_ID}`;
  await page.route(`${base}/runs/${RUN_ID}/interactions/authority`, (route) => json(route, { approve: true, deny: true }));
  await page.route(`${base}/runs/${RUN_ID}/interactions/*/decision`, (route) => {
    server.decisions.push(route.request().postDataJSON());
    server.stream.releaseAll();
    return json(route, { receipt: { commandId: 'c1' }, interaction: { interactionId: 'ix', decision: 'approved' } });
  });
  await page.route(`${base}/runs/${RUN_ID}/timeline`, (route) =>
    json(route, {
      runId: RUN_ID,
      parentRunId: null,
      status: server.finished ? 'completed' : 'running',
      parts: ENGINE_SAVED.parts,
      engine: { taskId: RUN_ID, frames: ENGINE_SAVED.parts[0]?.frames ?? [], truncated: false },
      approvals: [],
      children: [],
      merge: null,
      budget: BUDGET,
    }),
  );
  await page.route(`${base}/workspace/versions`, (route) => json(route, server.versions()));
  await page.route(`${base}/workspace/versions/*/restore`, (route) => {
    server.restores.push({ url: new URL(route.request().url()).pathname, body: route.request().postDataJSON() });
    server.head += 1;
    return json(route, { workspaceId: `ws:${SESSION_ID}`, revision: server.head, restoredFrom: 1, replayed: false });
  });
}

/** Opens the answer's "View activity" drawer. */
async function openActivity(page: Page) {
  await page.getByRole('button', { name: 'View activity' }).last().click();
  const drawer = page.getByTestId('activity-panel');
  await expect(drawer).toBeVisible({ timeout: 15_000 });
  return drawer;
}

test.describe('Chat — a durable engine run, live and after a reload (U3, CTL-4, needs the e2e stack)', () => {
  test('PW-1/PW-2/PW-4: the run is on the timeline, the approval is decided from its card, and it all comes back after a reload', async ({ page }) => {
    const server = new EngineRunServer(ENGINE_FRAMES);
    server.stream.releaseThrough((f) => f.type === 'CUSTOM' && f.data.name === 'approval_requested');
    await mockBackend(page, server, () => stored(ENGINE.query, server.finished ? ENGINE_SAVED : null));
    await openConversation(page, SESSION_ID);

    await expect(page.getByTestId('activity-summary')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('python report.py', { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Code task' })).toHaveCount(0);
    const approval = page.getByRole('region', { name: 'Jira create issue?' });
    await expect(approval).toBeVisible();
    await approval.getByRole('button', { name: 'Allow once: Jira create issue' }).focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => server.decisions.length).toBe(1);
    expect(server.decisions[0]).toMatchObject({ decision: 'approved' });

    const summary = page.getByTestId('activity-summary');
    await expect(summary).toContainText('Ran 1 command', { timeout: 15_000 });
    await expect(summary).toContainText('Created q3-report.md');

    await page.reload();
    await expect(summary).toContainText('Ran 1 command', { timeout: 15_000 });
    await expect(page.getByRole('region', { name: 'Code task' })).toHaveCount(0);
    await summary.click();
    await expect(page.getByText('python report.py', { exact: true })).toBeVisible();
    const file = page.getByRole('list', { name: 'Files changed' }).getByRole('button', { name: /out\/q3-report\.md/ });
    await expect(file).toContainText('added');
    await file.click();
    await expect(page.getByRole('region', { name: 'Changes in out/q3-report.md' })).toContainText('+Revenue up 4%');
    // UXINT-2: once decided, the saved approval is one receipt line in the steps; the full card is in View activity.
    const saved = page.getByTestId('interaction-receipt');
    await expect(saved).toContainText('Allowed once by you');
    await expect(saved).toHaveAttribute('data-status', 'approved');
    await expect(page.getByText(ENGINE_SAVED.answer.split('\n')[0] as string).first()).toBeVisible();
    await expect((await openActivity(page)).getByTestId('run-cost-usd')).toHaveText('Spent $0.42 of $5.00', { timeout: 15_000 });
  });

  test('PW-3: restoring an older workspace version, from View activity, adds a new version and leaves the others', async ({ page }) => {
    const server = new EngineRunServer(ENGINE_FRAMES);
    server.stream.releaseAll();
    await mockBackend(page, server, () => stored(ENGINE.query, ENGINE_SAVED));
    await openConversation(page, SESSION_ID);
    await expect(page.getByTestId('activity-summary')).toBeVisible({ timeout: 15_000 });
    const drawer = await openActivity(page);
    const rows = drawer.getByTestId('workspace-version');
    await expect(rows).toHaveCount(2, { timeout: 15_000 });
    await expect(rows.first()).toContainText('Current');
    await drawer.getByRole('button', { name: 'Restore version 1' }).click();
    await drawer.getByRole('button', { name: 'Restore', exact: true }).click();
    await expect.poll(() => server.restores.length).toBe(1);
    expect(server.restores[0]).toEqual({
      url: `/api/v1/conversations/${SESSION_ID}/workspace/versions/1/restore`,
      body: { baseRevision: 2, idempotencyKey: 'restore-1-on-2' },
    });
    await expect(rows).toHaveCount(3);
    await expect(rows.first()).toHaveAttribute('data-revision', '3');
    await expect(rows.first()).toContainText('Current');
    await expect(rows.nth(1)).toHaveAttribute('data-revision', '2');
    await expect(drawer.getByTestId('workspace-versions-status')).toHaveText('Version 1 restored as version 3.');
  });

  test('PW-1, CTL-6 C6-2: the code_task handoff is the job card named by its goal, whose own steps and files open', async ({ page }) => {
    const server = new EngineRunServer(ENGINE_FRAMES);
    server.stream.releaseAll();
    await mockBackend(page, server, () => stored(PARENT.query, PARENT_SAVED));
    await page.route(`**/api/v1/conversations/${SESSION_ID}/runs/run-u3-child/timeline`, (route) =>
      json(route, {
        runId: 'run-u3-child',
        parentRunId: PARENT.runId,
        status: 'completed',
        parts: [],
        engine: {
          taskId: 'run-u3-child',
          truncated: false,
          frames: [
            { tool: 'code_task', taskId: 'run-u3-child', kind: 'started', text: 'Analyse in/q3.csv', data: {} },
            { tool: 'code_task', taskId: 'run-u3-child', kind: 'command', text: 'python analyse.py', data: { callId: 'c1' } },
            { tool: 'code_task', taskId: 'run-u3-child', kind: 'command_done', text: '', data: { callId: 'c1', exitCode: 0 } },
            {
              tool: 'code_task',
              taskId: 'run-u3-child',
              kind: 'diff',
              text: 'out/analysis.csv',
              data: { change: 'added', diff: '--- a/out/analysis.csv\n+++ b/out/analysis.csv\n@@ -0,0 +1,2 @@\n+region,total\n+west,42\n' },
            },
            { tool: 'code_task', taskId: 'run-u3-child', kind: 'finished', text: 'analysis.csv written', data: { success: true } },
          ],
        },
        approvals: [],
        children: [],
        merge: {
          status: 'partial',
          into: `ws:${SESSION_ID}`,
          revision: 4,
          forkRevision: 2,
          merged: ['out/analysis.csv'],
          conflicts: [{ path: 'notes.md', kind: 'both_changed' }],
          repositoryConflicts: [],
        },
        budget: BUDGET,
      }),
    );
    await openConversation(page, SESSION_ID);
    await page.getByText('Delegated to 1 sub-agent', { exact: false }).click({ timeout: 15_000 });
    // The handoff is a job card named by its task, not by an internal role name (CLASSIC-JOBS D2).
    await expect(page.getByTestId('job-card').getByText('Analyse in/q3.csv', { exact: true })).toBeVisible();
    const toggle = page.getByTestId('job-card').filter({ hasText: 'Analyse in/q3.csv' }).getByTestId('sub-agent-toggle');
    await toggle.focus();
    await page.keyboard.press('Enter');
    const panel = page.getByTestId('child-run-panel');
    await expect(panel.getByText('python analyse.py', { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(panel.getByRole('region', { name: 'Code task' })).toHaveCount(0);
    // CTRESULTS: the child's file is its own step, "Created <path>", whose diff opens in place.
    const file = panel.getByTestId('engine-file').filter({ hasText: 'out/analysis.csv' });
    await expect(file).toContainText('Created');
    await expect(file.getByTestId('code-task-diff')).toHaveCount(0);
    await file.getByRole('button', { name: /out\/analysis\.csv/ }).click();
    await expect(file.getByTestId('code-task-diff')).toContainText('+west,42');
    await expect(panel.getByTestId('child-merge-report')).toContainText('notes.md · changed on both sides');
    await expect(panel.getByRole('group', { name: 'Cost' })).toContainText('Spent $0.42 of $5.00');
  });
});
