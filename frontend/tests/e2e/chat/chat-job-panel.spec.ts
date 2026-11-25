/**
 * chat-job-panel.spec.ts — CJ-3 (classic jobs plan §4.2, D4, D5, D11, D14): the job details panel in a real browser.
 *
 * A saved answer that handed work to an engine job (U3's parent journal: its `sub_agent` part names the child run)
 * is opened by a link that names the job (`&job=…`). The job's own routes are page.route() mocks in the shapes Node
 * serves: its stream (U3's engine frames, as the child's), its activity (UX5 §3.4), brief, timeline and cancel. No
 * turn is sent, so no model is reached; the `setup` project signs in against the e2e stack.
 *
 * With UI_CASE_SHOTS=<dir>, each state saves its pictures there: <state>-<desktop|phone>-<light|dark>.png.
 */
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';
import { MODEL_INFO, json, sse, type SseFrame } from './durable-conversation.mock';

const JOURNALS = join(__dirname, '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals');
const vector = <T>(name: string): T => JSON.parse(readFileSync(join(JOURNALS, name), 'utf8')) as T;

const PARENT = vector<{ runId: string; sessionId: string; query: string }>('u3_parent_with_engine_child.journal.json');
const PARENT_SAVED = vector<{ answer: string; parts: Array<Record<string, unknown>> }>('u3_parent_with_engine_child.parts.json');
const ENGINE_FRAMES = vector<{ frames: Array<{ id: string; frame: Record<string, unknown> }> }>('u3_engine_run.frames.json').frames;

const SESSION_ID = PARENT.sessionId;
const JOB_ID = 'run-u3-child';
const SHOTS = process.env.UI_CASE_SHOTS ?? '';

/** The engine run's frames, as the child's own stream serves them: the same journal, named for the child. */
const JOB_FRAMES: SseFrame[] = ENGINE_FRAMES.map(({ id, frame }) => {
  const named = JSON.parse(JSON.stringify(frame).replaceAll('run-u3-engine', JOB_ID)) as Record<string, unknown>;
  const { type, ...data } = named;
  return { id, type: String(type), data };
});
const UNTIL_COMMAND = JOB_FRAMES.findIndex((f) => f.type === 'CUSTOM' && (f.data.value as { kind?: string })?.kind === 'command_done') + 1;

const COMMAND = {
  commandId: 'c1',
  source: 'engine',
  command: 'python report.py',
  status: 'completed',
  exitCode: 0,
  startedAt: 1_760_000_000_000,
  durationMs: 1_250,
  stdout: 'wrote out/q3-report.md\n',
  stderr: '',
  truncated: false,
  liveDropped: 0,
  hasLog: false,
};

const activityView = (status: 'running' | 'completed') => ({
  runId: JOB_ID,
  status,
  engine: true,
  commands: [COMMAND],
  omittedCommands: 0,
  tools: [],
  tasks: [],
  files: [{ path: 'out/q3-report.md', change: 'added', diff: '--- /dev/null\n+++ b/out/q3-report.md\n@@ -0,0 +1 @@\n+# Q3 report\n' }],
  followups: [],
});

const BRIEF = {
  runId: JOB_ID,
  goal: 'Analyse in/q3.csv and write the quarterly report to out/q3-report.md',
  sent: { files: ['in/q3.csv'], sources: [], sourcesComplete: true, knowledge: { apps: [], collections: [] }, skills: ['pdf'], continuesRunId: null },
  withheld: [
    { what: 'credentials', reason: 'outside_sandbox' },
    { what: 'conversation', reason: 'task_only' },
  ],
  reach: { internet: 'none', sandbox: true, tools: [], allowedWithoutAsking: 0 },
};

/**
 * BGUI-10: a recorded IPL report hand-off. The goal is the whole prompt the chat composed for the engine (as a job admitted
 * before BGUI-10 kept it); `task` is its parts, as the harness keeps them now. The Brief shows the parts only.
 */
const IPL_REQUEST = 'Can you prepare a pdf report of ipl 2026 event';
const IPL_NOTES =
  'Prepare a PDF report on the IPL 2026 season: the champion and the final, the points table, the top run scorers ' +
  'and wicket takers, and the season\'s standout moments. Use **reportlab**, keep it to about six pages, and cite ' +
  'the sources you read.';
const IPL_DUE = new Date(Date.now() + 56 * 60_000);
const BRIEF_IPL = {
  ...BRIEF,
  goal:
    `${IPL_REQUEST}\n\nThis task's time and money: deliver your result by 10:12 UTC (about 56 minutes from now), with at most USD 5.00 of model use.\n\n` +
    `Notes from the chat agent that handed this task to you:\n${IPL_NOTES}\n\n` +
    'Tool calls this chat already made before handing over, with their results. Use them: do not call a tool again with the same arguments; ' +
    'call one only for what they do not cover, or to read in full a result shortened here.\n- load_skill({"name": "pdf"}):\n  ' +
    `{"name": "pdf", "content": "---\\nname: pdf\\ndescription: Create and edit PDF files\\n---\\n# PDF\\n${'Use reportlab to lay out pages. '.repeat(40)}"}`,
  task: {
    request: IPL_REQUEST,
    notes: IPL_NOTES,
    earlier: '',
    fileName: null,
    dueAt: IPL_DUE.toISOString().replace(/\.\d{3}Z$/, 'Z'),
    maxCostUsd: 5,
    carried: [
      { tool: 'search_tools', failed: false },
      { tool: 'load_skill', failed: false, skill: 'pdf' },
    ],
  },
};
const NOTHING_SPENT = {
  cap: { usdMicros: 5_000_000, tokens: null },
  committed: { usdMicros: 0, tokens: 0 },
  reserved: { usdMicros: 0, tokens: 0 },
  available: { usdMicros: 5_000_000, tokens: null },
  exhausted: false,
};

interface Job {
  brief?: Record<string, unknown>;
  ended: boolean;
  cancels: number;
  activityOpened: number;
  briefReads: number;
  owner: boolean;
}

async function mockJob(page: Page, job: Job): Promise<void> {
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
  await page.route('**/api/v1/conversations*', (route) =>
    route.request().method() === 'GET'
      ? json(route, { conversations: [], source: 'owned', pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false } })
      : route.fallback(),
  );
  const base = `/api/v1/conversations/${SESSION_ID}`;
  const at = (suffix: string) => (url: URL) => url.pathname.replace(/\/$/, '') === `${base}${suffix}`;
  await page.route(at(`/runs/${JOB_ID}/stream`), (route) =>
    route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: sse(job.ended ? JOB_FRAMES : JOB_FRAMES.slice(0, UNTIL_COMMAND)) }),
  );
  await page.route(at(`/runs/${JOB_ID}/activity`), (route) => {
    job.activityOpened += 1;
    const events: Array<[string, unknown]> = [['snapshot', activityView(job.ended ? 'completed' : 'running')], ...(job.ended ? [['end', { status: 'completed' }] as [string, unknown]] : [])];
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
    });
  });
  await page.route(at(`/runs/${JOB_ID}/brief`), (route) => {
    job.briefReads += 1;
    return json(route, job.brief ?? BRIEF);
  });
  await page.route(at(`/runs/${JOB_ID}/brief/history`), (route) => json(route, { runId: JOB_ID, startedAs: BRIEF.goal, changes: [] }));
  await page.route(at(`/runs/${JOB_ID}/timeline`), (route) =>
    json(route, {
      runId: JOB_ID,
      parentRunId: PARENT.runId,
      status: job.ended ? 'completed' : 'running',
      parts: [],
      engine: null,
      approvals: [],
      children: [],
      merge: null,
      budget: job.brief ? NOTHING_SPENT : null,
      ...(job.ended
        ? {
            digest: {
              outcome: 'answered',
              actions: { total: 1, failed: 0, refused: 0, tools: [] },
              commands: { total: 1, failed: 0 },
              records: { read: 0, changed: 0 },
              artifacts: [{ name: 'q3-report.md', recordId: 'art-q3' }],
              files: { added: 1, modified: 0, deleted: 0 },
              approvals: { asked: 1, approved: 1, denied: 0, unanswered: 0 },
              questions: { asked: 0, open: false },
              delegated: 0,
              cost: null,
              durationMs: 74_000,
            },
          }
        : {}),
    }),
  );
  await page.route(at(`/runs/${JOB_ID}/cancel`), (route) => {
    job.cancels += 1;
    return json(route, { receipt: { runId: JOB_ID, action: 'cancel', accepted: true } });
  });
  await page.route(at(`/runs/${JOB_ID}/interactions/authority`), (route) => json(route, { approve: true, deny: true }));
  await page.route(at('/workspace/versions'), (route) => json(route, { workspaceId: `ws:${SESSION_ID}`, head: 0, versions: [] }));
  await page.route(at(''), (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const conversation = buildAguiConversation({
      conversationId: SESSION_ID,
      userMessageId: 'msg-user-cj3',
      botMessageId: 'msg-bot-cj3',
      question: PARENT.query,
      answer: PARENT_SAVED.answer,
      modelInfo: MODEL_INFO,
    });
    const parts = job.ended
      ? PARENT_SAVED.parts
      : PARENT_SAVED.parts.map((p) => (p.type === 'sub_agent' ? { ...p, status: 'running' } : p));
    (conversation.messages[1] as Record<string, unknown>).parts = parts;
    return json(route, {
      conversation: {
        ...conversation,
        id: SESSION_ID,
        access: job.owner ? { isOwner: true, accessLevel: 'OWNER' } : { isOwner: false, accessLevel: 'READ' },
        activeRunId: null,
      },
      filters: {},
      meta: {},
    });
  });
}

const panel = (page: Page) => page.getByTestId('activity-panel');

async function shoot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });
  // The pane slides in: a picture mid-slide shows the chat half covered. Spinners loop for ever, so only finite ones count.
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity),
  );
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}

const LOOKS = [
  { name: 'desktop-light', viewport: { width: 1440, height: 960 }, dark: false },
  { name: 'desktop-dark', viewport: { width: 1440, height: 960 }, dark: true },
  { name: 'phone-light', viewport: { width: 390, height: 844 }, dark: false },
  { name: 'phone-dark', viewport: { width: 390, height: 844 }, dark: true },
] as const;

async function look(page: Page, l: (typeof LOOKS)[number]): Promise<void> {
  await page.setViewportSize(l.viewport);
  await page.emulateMedia({ colorScheme: l.dark ? 'dark' : 'light' });
  await page.addInitScript((d) => localStorage.setItem('pipeshub-theme-preference', d ? 'dark' : 'light'), l.dark);
}

test.describe('Chat — the job details panel (CJ-3, needs the e2e stack)', () => {
  for (const l of LOOKS) {
    test(`a link opens the job's panel; Stop is one click; a reload keeps it (${l.name})`, async ({ page }) => {
      const job: Job = { ended: false, cancels: 0, activityOpened: 0, briefReads: 0, owner: true };
      await mockJob(page, job);
      await look(page, l);
      await page.goto(`/chat/?conversationId=${SESSION_ID}&job=${JOB_ID}`);

      await expect(panel(page).getByTestId('job-panel-title')).toHaveText('Analyse in/q3.csv');
      await expect(panel(page).getByTestId('job-panel-status')).toContainText('Working');
      // A live job opens on its Activity tab (BGUI-3: the design's tabs, one view at a time).
      const tabs = panel(page).getByRole('tablist', { name: 'Job sections' });
      await expect(tabs.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true');
      await expect(panel(page)).toBeFocused();
      await shoot(page, `01-working-${l.name}`);

      await tabs.getByRole('tab', { name: 'Brief' }).click();
      await expect(panel(page).getByTestId('job-brief-goal')).toHaveText(BRIEF.goal);
      await expect(page).toHaveURL(new RegExp(`job=${JOB_ID}&section=brief`));
      await tabs.getByRole('tab', { name: 'Machine' }).click();
      await expect(panel(page).getByTestId('activity-section-commands')).toContainText('Terminal');
      await expect(page).toHaveURL(new RegExp(`job=${JOB_ID}&section=machine`));
      await shoot(page, `02-machine-${l.name}`);
      if (l.viewport.width < 500) {
        const box = await tabs.getByRole('tab', { name: 'Machine' }).boundingBox();
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      }

      const stop = panel(page).getByTestId('job-panel-stop');
      await expect(stop).toHaveAccessibleName('Stop job: Analyse in/q3.csv');
      if (l.viewport.width < 500) {
        const box = await stop.boundingBox();
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
        expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
      }
      await stop.click();
      await expect.poll(() => job.cancels).toBe(1);

      await page.reload();
      await expect(panel(page).getByTestId('job-panel-title')).toHaveText('Analyse in/q3.csv');
      await expect(page).toHaveURL(new RegExp(`job=${JOB_ID}&section=machine`));

      // The tab the URL names takes focus once it is drawn, so Escape is the panel's.
      await expect(panel(page).getByRole('tab', { name: 'Machine' })).toBeFocused();
      if (l.viewport.width >= 500) {
        await page.keyboard.press('Escape');
        await expect(panel(page)).toHaveCount(0);
        await expect(page).not.toHaveURL(/job=/);
      }
    });

    test(`an ended job shows Done, its brief and its outputs (${l.name})`, async ({ page }) => {
      const job: Job = { ended: true, cancels: 0, activityOpened: 0, briefReads: 0, owner: true };
      await mockJob(page, job);
      await look(page, l);
      // A link written before the tabs (Files) named the job's files: it opens on Outputs (BGUI-11), where an ended
      // job's delivered files are, as the answer's file cards.
      await page.goto(`/chat/?conversationId=${SESSION_ID}&job=${JOB_ID}&section=files`);
      await expect(page).toHaveURL(new RegExp(`job=${JOB_ID}&section=outputs`));

      await expect(panel(page).getByTestId('job-panel-status')).toContainText('Done');
      await expect(panel(page).getByTestId('job-panel-stop')).toHaveCount(0);
      await expect(panel(page).getByRole('tab', { name: 'Outputs 1' })).toHaveAttribute('aria-selected', 'true');
      const card = panel(page).getByTestId('job-outputs-delivered').getByTestId('artifact-card');
      await expect(card).toHaveCount(1);
      await expect(card).toContainText('q3-report.md');
      await expect(card.getByRole('button', { name: 'Download artifact' })).toBeVisible();
      await shoot(page, `03-done-outputs-${l.name}`);
      await panel(page).getByRole('tab', { name: 'Machine' }).click();
      await expect(panel(page).getByTestId('activity-section-workspace')).toBeVisible();
      await shoot(page, `03-done-machine-${l.name}`);
      await panel(page).getByRole('tab', { name: 'Brief' }).click();
      await panel(page).getByRole('button', { name: 'What it was given' }).click();
      await expect(panel(page).getByTestId('job-brief')).toContainText('Passwords, keys and sign-ins');
      await shoot(page, `04-done-brief-${l.name}`);
      expect(job.briefReads).toBe(1);
    });
  }

  for (const l of LOOKS) {
    test(`BGUI-10: an engine job's Brief is its task, its limits and what it started with, never the engine's prompt (${l.name})`, async ({ page }) => {
      const job: Job = { brief: BRIEF_IPL, ended: false, cancels: 0, activityOpened: 0, briefReads: 0, owner: true };
      await mockJob(page, job);
      await look(page, l);
      await page.goto(`/chat/?conversationId=${SESSION_ID}&job=${JOB_ID}&section=brief`);
      const brief = panel(page).getByTestId('job-panel-brief');
      await expect(brief).toContainText(IPL_REQUEST, { timeout: 30_000 });
      await shoot(page, `10-brief-ipl-${l.name}`);

      await expect(panel(page).getByTestId('job-brief-goal')).toContainText('Prepare a PDF report on the IPL 2026 season');
      await expect(panel(page).getByTestId('job-brief-goal').locator('strong')).toHaveText('reportlab');
      await expect(panel(page).getByTestId('job-brief-asked')).toHaveText(`You asked: “${IPL_REQUEST}”`);
      await expect(panel(page).getByTestId('job-brief-limits')).toContainText(/Due by .+ · Up to USD\s5\.00/);
      for (const leaked of ['time and money', 'Notes from the chat agent', 'Tool calls this chat', 'load_skill', '{"name"', 'Spent $0.00']) {
        await expect(brief).not.toContainText(leaked);
      }
      await expect(panel(page).getByTestId('run-cost')).toHaveCount(0);
      // What it started with, folded: the skill it loaded by name; the tool lookup is plumbing and not listed.
      await expect(panel(page).getByTestId('job-brief-started')).toHaveCount(0);
      const started = panel(page).getByRole('button', { name: 'What it started with' });
      await started.click();
      await expect(panel(page).getByTestId('job-brief-started').locator('li')).toHaveText([/Loaded the PDF skill$/]);
      if (l.viewport.width < 500) expect((await started.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
      await shoot(page, `11-brief-ipl-started-${l.name}`);
      // Copied, the Brief never reads as icon names.
      const copied = await brief.evaluate((el) => {
        const range = document.createRange();
        range.selectNodeContents(el);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
        return selection?.toString() ?? '';
      });
      expect(copied).not.toMatch(/expand_more|expand_less|schedule|school/);
    });
  }

  test('a shared chat’s viewer sees the status and none of the owner’s sections (D14)', async ({ page }) => {
    const job: Job = { ended: false, cancels: 0, activityOpened: 0, briefReads: 0, owner: false };
    await mockJob(page, job);
    await look(page, LOOKS[0]);
    await page.goto(`/chat/?conversationId=${SESSION_ID}&job=${JOB_ID}`);
    await expect(panel(page).getByTestId('job-panel-owner-only')).toHaveText('Only the person who started this job can see its details');
    await expect(panel(page).getByTestId('job-panel-status')).toContainText('Working');
    await expect(panel(page).getByTestId('job-panel-stop')).toHaveCount(0);
    await expect(panel(page).getByRole('tablist')).toHaveCount(0);
    await shoot(page, '05-viewer-desktop-light');
    expect(job.activityOpened).toBe(0);
    expect(job.briefReads).toBe(0);
  });
});
