/**
 * chat-durable-engine-e2e.spec.ts — E2E2 DE-1..DE-6: a durable engine run on the real path, nothing mocked.
 *
 * Browser → Node (durable admission, relay) → `app.harness_main` (the worker, EngineKernel) → a broker sandbox
 * running the lane's engine (E2E_ENGINE, opencode by default) → the egress proxy → the model gateway (the scripted model) and the worker's tool gateway (the
 * scripted SaaS, an MCP server attached to the engine agent) → U3's timeline. The engine's directives travel in the
 * user's message (the engine's prompt); `hold` keeps its next model call open until the spec releases it.
 *
 * The `harness-engine` project on `deployment/e2e/run.sh durable` (it sets the variables below), nightly in CI.
 *
 * CTL-6: the engine agent's turn runs on the loop, whose scripted model hands the whole script to `code_task`; the
 * engine child runs it as the engine run did, and the turn answers with the script's `say` once the child joins.
 * The engine's approvals, versions and crash recovery are the child's.
 */
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { request as playwrightRequest } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { test, expect } from '../fixtures/api-context.fixture';
import {
  activeRunId,
  allowOnceButton,
  chatInput,
  approvalCard,
  ENGINE_SHELL_TOOLS,
  openFinishedActivity,
  recordPostBodies,
  sendTurn,
} from '../harness/support/lane';

test.describe.configure({ mode: 'serial' });

const AGENT_KEY = process.env.E2E_ENGINE_AGENT_KEY;
const LLM_URL = process.env.E2E_LLM_URL;
const SAAS_URL = process.env.E2E_SAAS_URL;
const CONTROL = process.env.E2E_LLM_CONTROL ?? '';
const CONTROL_DIR = process.env.E2E_CONTROL_DIR ?? '';
const TOOL = 'create_ticket';

type Effect = { tool: string; argsDigest: string; ticket: string; peer: string | null };
type ModelRequest = { path: string; status: number; kind?: string; tools?: string[] };
type Amount = { usdMicros: number | null; tokens: number | null };
type Timeline = {
  status: string;
  budget: { cap: Amount; committed: Amount; reserved: Amount } | null;
  approvals: Array<{ status: string; tool?: string }>;
  children: Array<{ runId: string; status: string }>;
};
type Versions = { head: number; versions: Array<{ revision: number; reason: string; parentRevision: number | null }> };

/**
 * One engine task: a file through the shell, a ticket through the agent's write tool (the workspace is saved
 * before it, version 1), notes through the engine's write tool (saved at the turn's end, version 2), a held
 * call, a summary.
 */
function task(file: string, ticket: string): { prompt: string; content: string; summary: string } {
  const content = 'region,revenue\nnorth,120\nsouth,80\ntotal,200\n';
  const summary = `The Q3 table is in ${file} and ticket "${ticket}" is filed.`;
  const prompt = [
    `Write the Q3 table to ${file} and file a ticket for it.`,
    `shell mkdir -p out && printf 'region,revenue\\nnorth,120\\nsouth,80\\ntotal,200\\n' > out/${file}`,
    `tool ${TOOL} ${JSON.stringify({ title: ticket, body: `out/${file} is ready` })}`,
    `write notes-${file}.md Filed ticket ${ticket}.`,
    'hold',
    `say ${summary}`,
  ].join('\n');
  return { prompt, content, summary };
}

async function control(baseURL: string | undefined): Promise<APIRequestContext> {
  return playwrightRequest.newContext({ baseURL, extraHTTPHeaders: { 'X-E2E-Control': CONTROL } });
}

async function effects(saas: APIRequestContext): Promise<Effect[]> {
  return ((await (await saas.get('/__control/effects')).json()) as { effects: Effect[] }).effects;
}

async function holding(model: APIRequestContext): Promise<boolean> {
  return ((await (await model.get('/__control/holding')).json()) as { holding: boolean }).holding;
}

/** Sends the task to the engine agent from the page; the run is the conversation's running one (as a reattaching
 * client reads it), and no request the page sent names an account (Node chose it). */
async function ask(page: Page, api: APIRequestContext, prompt: string): Promise<{ sessionId: string; runId: string }> {
  const sent = recordPostBodies(page);
  const sessionId = await sendTurn(page, AGENT_KEY!, prompt);
  sessions.push(sessionId);
  const runId = await activeRunId(api, AGENT_KEY!, sessionId);
  expect(sent.join('\n')).not.toContain('modelAccountId');
  return { sessionId, runId };
}

/** The engine child the turn handed its script to (CTL-6). */
async function handOff(api: APIRequestContext, sessionId: string, runId: string): Promise<string> {
  let child: string | undefined;
  await expect
    .poll(async () => {
      child = (await timeline(api, sessionId, runId)).children[0]?.runId;
      return child ?? null;
    }, { message: 'the turn handed off to an engine child', timeout: 240_000 })
    .not.toBeNull();
  return child as string;
}

function sessionBase(sessionId: string): string {
  return `/api/v1/agents/${AGENT_KEY}/conversations/${sessionId}`;
}

async function timeline(api: APIRequestContext, sessionId: string, runId: string): Promise<Timeline> {
  const res = await api.get(`${sessionBase(sessionId)}/runs/${runId}/timeline`);
  expect(res.ok(), `timeline: ${res.status()}`).toBe(true);
  return (await res.json()) as Timeline;
}

/** The job's decided ask, read-only: its receipt at the card (UXINT-1) or, in its opened steps, its decided card. */
const decidedAsk = (scope: Page | Locator) =>
  scope.locator('[data-testid="interaction-receipt"][data-status="approved"], [data-testid="approval-card"][data-status="approved"]');

async function approve(page: Page): Promise<void> {
  const button = allowOnceButton(page, TOOL);
  await expect(button).toBeVisible({ timeout: 240_000 });
  await button.focus();
  await page.keyboard.press('Enter');
  // UXINT-1: a decided card folds into a one-line receipt where it was asked, so no card is left to read "approved".
  await expect(decidedAsk(page.getByTestId('job-card').first()).first()).toBeVisible({ timeout: 60_000 });
  await expect(approvalCard(page, TOOL)).toHaveCount(0);
}

/** Releases every held engine call until the run completed (a resumed turn may hold again). */
async function releaseUntilFinished(
  page: Page,
  model: APIRequestContext,
  api: APIRequestContext,
  run: { sessionId: string; runId: string },
  timeoutMs: number,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if ((await timeline(api, run.sessionId, run.runId)).status === 'completed') return;
    if (await holding(model)) await model.post('/__control/release');
    await page.waitForTimeout(1_000);
  }
  expect((await timeline(api, run.sessionId, run.runId)).status).toBe('completed');
}

/** The engine child's own steps, opened from its job card's step toggle (CTL-6, CLASSIC-JOBS D2). */
async function childPanel(page: Page) {
  await page.getByTestId('job-card').first().getByTestId('sub-agent-toggle').click();
  const panel = page.getByTestId('child-run-panel');
  await expect(panel).toBeVisible({ timeout: 30_000 });
  return panel;
}

/** The answer's "View activity" drawer (CTL-4: the run's cost and workspace versions are there). */
/** The drawer of an answer: the latest one, or the first (the first turn's, after a follow-up). */
async function openActivity(page: Page, which: 'first' | 'last' = 'last') {
  await page.getByRole('button', { name: 'View activity' })[which]().click();
  const drawer = page.getByTestId('activity-panel');
  await expect(drawer).toBeVisible({ timeout: 30_000 });
  return drawer;
}

/** The answer's "Files changed" row for a file the run wrote. */
function changedFile(page: Page, name: string) {
  return page.getByRole('list', { name: 'Files changed' }).getByRole('button', { name: new RegExp(name.replace('.', '\\.')) });
}

/** The cost line's own format (`formatSpend`): cents, or four places for a spend under a cent. */
function usd(micros: number | null): string {
  const value = micros ?? 0;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: value > 0 && value < 10_000 ? 4 : 2,
  }).format(value / 1_000_000);
}

function spent(budget: NonNullable<Timeline['budget']>): string {
  const cap = budget.cap.usdMicros;
  return `Spent ${usd(budget.committed.usdMicros)}${cap === null ? '' : ` of ${usd(cap)}`}`;
}

const sessions: string[] = [];

test.describe('Chat — a durable engine run end to end (E2E2, needs deployment/e2e/run.sh durable)', () => {
  test.beforeAll(() => {
    expect(AGENT_KEY && LLM_URL && SAAS_URL, 'the stack `run.sh durable` brings up').toBeTruthy();
  });

  // DE-7: deleting the conversations erases them; run.sh then checks their folders left the broker's volume.
  test.afterAll(async ({ apiContext }) => {
    for (const sessionId of sessions) {
      const res = await apiContext.delete(`/api/v1/agents/${AGENT_KEY}/conversations/${sessionId}`);
      expect(res.ok(), `deleting ${sessionId}: ${res.status()}`).toBe(true);
    }
    if (CONTROL_DIR) fs.writeFileSync(path.join(CONTROL_DIR, 'erased-sessions'), sessions.join('\n'));
  });

  test('DE-1..DE-4, DE-6: the engine writes a file and files a ticket after an approval; the timeline, cost and versions are real', async ({
    page,
    apiContext,
  }) => {
    test.setTimeout(600_000);
    const model = await control(LLM_URL);
    const saas = await control(SAAS_URL);
    await model.post('/__control/reset');
    await saas.post('/__control/reset');
    const { prompt, content, summary } = task('q3-report.csv', 'Q3 report ready');
    const { sessionId, runId } = await ask(page, apiContext, prompt);

    // DE-1 live, DE-2: the turn hands off; its engine child is a row on the timeline; the write tool asks; approved
    // from its card; it runs once.
    const header = page.getByTestId('activity-summary');
    await expect(header).toBeVisible({ timeout: 240_000 });
    await expect(page.getByRole('region', { name: 'Code task' })).toHaveCount(0);
    await expect(page.getByTestId('job-card').first()).toBeVisible({ timeout: 240_000 });
    const child = await handOff(apiContext, sessionId, runId);
    await approve(page);
    await expect.poll(async () => (await effects(saas)).length, { timeout: 120_000 }).toBe(1);
    await expect.poll(() => holding(model), { timeout: 120_000, message: 'the engine reaches its held call' }).toBe(true);
    await model.post('/__control/release');

    await releaseUntilFinished(page, model, apiContext, { sessionId, runId }, 240_000);
    await expect(page.getByText(summary).first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('button', { name: 'Download artifact' })).toHaveCount(1);
    if ((await header.getAttribute('aria-expanded')) === 'false') await header.click();
    await expect(changedFile(page, 'q3-report.csv')).toContainText('added');
    const done = await effects(saas);
    expect(done.map((e) => e.tool)).toEqual([TOOL]);

    // DE-4: the cost in View activity is the ledger's account of the run.
    const finishedRun = await timeline(apiContext, sessionId, runId);
    expect(finishedRun.status).toBe('completed');
    expect(finishedRun.budget, 'the run has a ledger account').not.toBeNull();
    expect((await timeline(apiContext, sessionId, child)).approvals.map((a) => a.status)).toEqual(['approved']);
    const cost = (await openActivity(page)).getByTestId('run-cost-usd');
    await expect(cost).toBeVisible({ timeout: 30_000 });
    await expect(cost).toHaveText(spent(finishedRun.budget!));
    expect(finishedRun.budget!.committed.tokens ?? 0, 'the engine model calls were charged').toBeGreaterThan(0);
    expect(finishedRun.budget!.committed.usdMicros ?? 0, 'at a price').toBeGreaterThan(0);

    // DE-1 after a reload: U3's timeline from the saved parts.
    await page.reload();
    await expect(page.getByText(summary).first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('region', { name: 'Code task' })).toHaveCount(0);
    await openFinishedActivity(page);
    await expect(changedFile(page, 'q3-report.csv')).toContainText('added');
    await childPanel(page);
    const job = page.getByTestId('job-card').first();
    await expect(decidedAsk(job)).toHaveCount(1);
    await expect(job.getByTestId('approval-card').getByRole('button')).toHaveCount(0);
    const download = page.getByRole('button', { name: 'Download artifact' });
    await expect(download).toHaveCount(1);
    const [file] = await Promise.all([page.waitForEvent('download', { timeout: 60_000 }), download.click()]);
    expect(file.suggestedFilename()).toBe('q3-report.csv');
    expect(fs.readFileSync(await file.path(), 'utf8')).toBe(content);

    // CTL-6 C6-8 on the real path: a follow-up turn hands off again, and its engine works on the file the first one
    // made (the session workspace the first hand-off merged into); that merge is the session's second version.
    await page.goto(`/chat/?agentId=${AGENT_KEY}&conversationId=${sessionId}`);
    await chatInput(page).waitFor({ timeout: 30_000 });
    await chatInput(page).fill(
      [
        'Check the table is still there.',
        'shell test -f out/q3-report.csv && echo seen > out/seen-q3.txt',
        'say The table is still there.',
      ].join('\n'),
    );
    await chatInput(page).press('Enter');
    // The second hand-off's file exists only if its engine found the first one's: its card is the turn's second.
    await expect(page.getByRole('button', { name: 'Download artifact' })).toHaveCount(2, { timeout: 240_000 });

    // DE-3: restoring an older workspace version adds a new one. The second card shows when its job publishes the
    // file, a moment before the turn joins the job and merges its workspace, so the merge is waited for, not assumed.
    const versions = async (): Promise<Versions> =>
      (await (await apiContext.get(`${sessionBase(sessionId)}/workspace/versions`)).json()) as Versions;
    await expect
      .poll(async () => (await versions()).versions.length, { message: 'each hand-off merged a version', timeout: 120_000 })
      .toBeGreaterThanOrEqual(2);
    const before = await versions();
    const older = before.versions[before.versions.length - 1]!.revision;
    const drawer = await openActivity(page, 'first');
    await expect(drawer.getByTestId('run-cost-usd')).toHaveText(spent(finishedRun.budget!), { timeout: 30_000 });
    const rows = drawer.getByTestId('workspace-version');
    await expect(rows).toHaveCount(before.versions.length, { timeout: 30_000 });
    await expect(rows.first()).toContainText('Current');
    await drawer.getByRole('button', { name: `Restore version ${older}` }).click();
    await drawer.getByRole('button', { name: 'Restore', exact: true }).click();
    await expect(drawer.getByTestId('workspace-versions-status')).toHaveText(
      `Version ${older} restored as version ${before.head + 1}.`,
      { timeout: 30_000 },
    );
    await expect(rows).toHaveCount(before.versions.length + 1);
    await expect(rows.first()).toHaveAttribute('data-revision', String(before.head + 1));
    await expect(rows.first()).toContainText('Current');
    const after = await versions();
    expect(after.head).toBe(before.head + 1);
    expect(after.versions[0]).toMatchObject({ revision: before.head + 1, reason: 'rollback', parentRevision: before.head });
    expect(after.versions.slice(1)).toEqual(before.versions);

    // DE-6: what reached the model and the SaaS.
    const requests = ((await (await model.get('/__control/requests')).json()) as { requests: ModelRequest[] }).requests;
    expect(requests.filter((r) => r.status !== 200), 'no call was refused').toEqual([]);
    const engine = requests.filter((r) => r.kind === 'engine');
    expect(engine.length).toBeGreaterThanOrEqual(3);
    expect(engine.every((r) => (r.tools ?? []).some((t) => ENGINE_SHELL_TOOLS.includes(t))), 'every engine call offers its shell').toBe(true);
    // ADR-OWD-07 D-LAZY-4: an MCP tool is in no engine list up front; the call named it and ran through call_tool.
    expect(engine.some((r) => (r.tools ?? []).some((t) => t.endsWith(TOOL))), 'an MCP schema was listed up front').toBe(false);
    expect(engine.some((r) => (r.tools ?? []).some((t) => t.endsWith('call_tool')))).toBe(true);
    await model.dispose();
    await saas.dispose();
  });

  test('DE-5: the worker is killed mid-run; a fresh worker resumes it; the effect ran once and the UI ends right', async ({
    page,
    apiContext,
  }) => {
    test.setTimeout(600_000);
    expect(CONTROL_DIR, 'the lane control directory (run.sh durable)').toBeTruthy();
    const model = await control(LLM_URL);
    const saas = await control(SAAS_URL);
    await model.post('/__control/reset');
    await saas.post('/__control/reset');
    const { prompt, summary } = task('q4-report.csv', 'Q4 report ready');
    const { sessionId, runId } = await ask(page, apiContext, prompt);

    await expect(page.getByTestId('activity-summary')).toBeVisible({ timeout: 240_000 });
    const child = await handOff(apiContext, sessionId, runId);
    await approve(page);
    await expect.poll(async () => (await effects(saas)).length, { timeout: 120_000 }).toBe(1);
    await expect.poll(() => holding(model), { timeout: 120_000 }).toBe(true);

    // The crash: SIGKILL, no drain; the lane's supervisor starts a fresh worker process.
    const done = path.join(CONTROL_DIR, 'kill-worker.done');
    fs.rmSync(done, { force: true });
    fs.writeFileSync(path.join(CONTROL_DIR, 'kill-worker'), runId);
    await expect.poll(() => fs.existsSync(done), { timeout: 30_000, message: 'the worker was killed' }).toBe(true);

    await releaseUntilFinished(page, model, apiContext, { sessionId, runId }, 420_000);
    await expect(page.getByText(summary).first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('button', { name: 'Download artifact' })).toHaveCount(1);
    const ran = await effects(saas);
    expect(ran.length, `the write ran once across both attempts: ${JSON.stringify(ran)}`).toBe(1);

    const run = await timeline(apiContext, sessionId, runId);
    expect(run.status).toBe('completed');
    expect((await timeline(apiContext, sessionId, child)).approvals.map((a) => a.status)).toEqual(['approved']);
    // run.sh checks the worker's log for the engine run: a second attempt claimed it and the re-call was `recorded`.
    fs.writeFileSync(path.join(CONTROL_DIR, 'crashed-run'), child);

    await page.reload();
    await expect(page.getByText(summary).first()).toBeVisible({ timeout: 60_000 });
    await openFinishedActivity(page);
    await expect(changedFile(page, 'q4-report.csv')).toContainText('added');
    await childPanel(page);
    await expect(decidedAsk(page.getByTestId('job-card').first())).toHaveCount(1);
    await model.dispose();
    await saas.dispose();
  });
});
