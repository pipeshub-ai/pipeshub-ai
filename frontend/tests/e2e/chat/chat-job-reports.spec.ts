/**
 * chat-job-reports.spec.ts — BGUI-6 (design `sandbox-thread.tsx` JobReport, §6B.42): finished background jobs report
 * back in the chat in the order they finished, under their cards and before the turn's answer, in a real browser.
 *
 * A saved chat (a reload: no job stream is replayed) with two requests. The first handed out three jobs: the CSV
 * export finished first with a file, the PDF job couldn't finish, and the notes job was cut short by its budget, so
 * its file is partial. The second handed out one job, which says how it ended and folds its working files (BGUI-11:
 * no report shows a job's own reply). One read of each request puts its jobs in finish order; each job's timeline
 * brings its files. BGUI-12: the EU job's reply in its panel names the source behind each citation chip (one its
 * timeline's answer citations hold, one the engine linked itself) and opens the chat answer's popover. Every API is
 * served here (page.route), sign-in
 * included: no stack, no model.
 *
 * With UI_CASE_SHOTS=<dir>, each state saves its pictures there: <state>-<desktop|phone>-<light|dark>.png.
 */
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';

const SESSION_ID = 'conv-bgui6';
const REQUEST = 'run-bgui6-request';
const LATER = 'run-bgui6-later';
const ASK = 'Close the quarter: export the invoices, render the PDF pack and write the board notes';
const ASK_LATER = 'Check the EU invoices for the wrong currency';
const SHOTS = process.env.UI_CASE_SHOTS ?? '';
const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'quick', modelFriendlyName: 'GPT-4o mini' };
const NOW = Date.now();
const BILLING_DOCS = 'https://docs.acme.example/billing/templates';
const ECB = 'https://www.ecb.europa.eu/stats/exchange/eurofxref';
/**
 * BGUI-13: the chat's own answer sums the EU job up in its words, links included: the page the job read is a stored
 * citation the parent adopted (ENGCITE2), the one it linked itself is not, as on the engine lane.
 */
const SECOND_ANSWER = `I checked the EU invoices: seven were formatted in dollars [1](${BILLING_DOCS}#:~:text=legacy%20template), and the new template is right [2](${ECB}).`;
const SECOND_CITATIONS = [
  {
    citationId: 'cit-billing',
    citationData: {
      _id: 'cit-billing',
      content: 'The legacy template prints every amount in USD, whatever the customer’s currency.',
      chunkIndex: 1,
      citationType: 'web|url',
      metadata: { recordId: BILLING_DOCS, recordName: 'Billing templates – Acme Docs', webUrl: BILLING_DOCS, origin: 'WEB_SEARCH', connector: 'WEB', recordType: 'WEBPAGE', mimeType: 'text/html', extension: '', previewRenderable: false, orgId: 'org-bgui6', recordVersion: 0 },
    },
  },
];

interface Job {
  runId: string;
  goal: string;
  engine: string;
  parent: string;
  status: 'completed' | 'failed';
  errorType?: string;
  durationMs: number;
  endedAt: number;
  answer?: string;
  /** Its answer's citations as its timeline carries them (BGUI-12). */
  citations?: Array<Record<string, unknown>>;
  cutShortBy?: string;
  artifacts: Array<{ name: string; recordId: string }>;
  changed?: number;
}

const JOBS: Job[] = [
  {
    runId: 'job-notes',
    goal: 'Write the board notes',
    engine: 'claude-code',
    parent: REQUEST,
    status: 'completed',
    durationMs: 74_000,
    endedAt: NOW - 20_000,
    answer: 'I drafted the revenue and churn sections; the cost section is missing.',
    cutShortBy: 'budget',
    artifacts: [{ name: 'board-notes.md', recordId: 'rec-notes' }],
  },
  {
    runId: 'job-csv',
    goal: 'Export the Q3 invoices',
    engine: 'codex',
    parent: REQUEST,
    status: 'completed',
    durationMs: 18_000,
    endedAt: NOW - 60_000,
    answer: 'Exported 1,284 invoices.',
    artifacts: [{ name: 'q3-invoices.csv', recordId: 'rec-csv' }],
  },
  {
    runId: 'job-pdf',
    goal: 'Render the PDF pack',
    engine: 'codex',
    parent: REQUEST,
    status: 'failed',
    errorType: 'worker_lost',
    durationMs: 31_000,
    endedAt: NOW - 40_000,
    artifacts: [],
  },
  {
    runId: 'job-eu',
    goal: 'Check the EU invoices',
    engine: 'claude-code',
    parent: LATER,
    status: 'completed',
    durationMs: 42_000,
    endedAt: NOW - 5_000,
    // Cited as an engine writes it: a page it read, numbered by the answer pipeline (stored), and a page of its own
    // that no tool read (no stored citation: named by its site, BGUI-12).
    answer: `Seven EU invoices were formatted in dollars [1](${BILLING_DOCS}#:~:text=legacy%20template). All seven come from the **legacy billing template**, which ignores the customer’s currency; the new template is right [2](https://www.ecb.europa.eu/stats/exchange/eurofxref).`,
    citations: [
      {
        content: 'The legacy template prints every amount in USD, whatever the customer’s currency.',
        chunkIndex: 1,
        citationType: 'web|url',
        metadata: { recordId: BILLING_DOCS, recordName: 'Billing templates – Acme Docs', webUrl: BILLING_DOCS, origin: 'WEB_SEARCH', connector: 'WEB', recordType: 'WEBPAGE', mimeType: 'text/html' },
      },
    ],
    artifacts: [],
    changed: 2,
  },
];

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

function fakeToken(): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'none', typ: 'JWT' })}.${part({ userId: 'user-bgui6', orgId: 'org-bgui6', email: 'bgui6@example.com', exp: 4_102_444_800 })}.sig`;
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
  const parts = (parent: string, answer: string) => [
    { type: 'text', content: answer, runId: parent, isFinal: true },
    ...JOBS.filter((j) => j.parent === parent).map((j) => ({
      type: 'sub_agent',
      runId: j.runId,
      parentRunId: parent,
      codeTaskGoal: j.goal,
      codeTaskHandoff: { engine: j.engine },
      status: j.status,
      durationMs: j.durationMs,
      ...(j.errorType ? { errorType: j.errorType } : {}),
      parts: [],
    })),
  ];
  const first =
    'The invoice export is ready. The PDF pack could not be rendered, so it is not included. The board notes are partial: the cost section is missing because that agent ran out of budget. Send “continue” to finish them.';
  const second = SECOND_ANSWER;
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
      message('msg-b2', 'bot_response', second, { parts: parts(LATER, second), runId: LATER, status: 'complete', citations: SECOND_CITATIONS }),
    ],
  };
}

function timeline(job: Job) {
  return {
    runId: job.runId,
    parentRunId: job.parent,
    status: job.status,
    parts: job.answer ? [{ type: 'text', isFinal: true, content: job.answer, ...(job.cutShortBy ? { truncatedBy: job.cutShortBy } : {}) }] : [],
    ...(job.answer ? { answer: { citations: job.citations ?? [] } } : {}),
    engine: null,
    approvals: [],
    children: [],
    merge: null,
    budget: null,
    codingEngine: job.engine,
    digest: {
      outcome: job.status === 'failed' ? 'failed' : 'answered',
      ...(job.cutShortBy ? { cutShortBy: job.cutShortBy } : {}),
      actions: { total: 0, failed: 0, refused: 0, tools: [] },
      commands: { total: 0, failed: 0 },
      records: { read: 0, changed: 0 },
      artifacts: job.artifacts,
      files: { added: 0, modified: job.changed ?? 0, deleted: 0, working: job.changed ?? 0 },
      approvals: { asked: 0, approved: 0, denied: 0, unanswered: 0 },
      questions: { asked: 0, open: false },
      delegated: 0,
      cost: null,
      durationMs: job.durationMs,
    },
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
  await page.route('**/api/v1/users/user-bgui6', (route) =>
    json(route, { _id: 'user-bgui6', fullName: 'Ada Lovelace', firstName: 'Ada', lastName: 'Lovelace', email: 'bgui6@example.com', hasLoggedIn: true, role: 'admin' }),
  );
  await page.route('**/api/v1/conversations*', (route) =>
    json(route, { conversations: [], source: 'owned', pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false } }),
  );
  const saved = conversation();
  await page.route((url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${SESSION_ID}`, (route) => json(route, { conversation: saved, filters: {}, meta: {} }));
  for (const job of JOBS) {
    const base = `/api/v1/conversations/${SESSION_ID}/runs/${job.runId}`;
    await page.route((url) => url.pathname === `${base}/timeline`, (route) => json(route, timeline(job)));
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
  }
  for (const request of [REQUEST, LATER]) {
    await page.route((url) => url.pathname === `/api/v1/conversations/${SESSION_ID}/runs/${request}/jobs`, (route) => {
      seen.requestReads += 1;
      const mine = JOBS.filter((j) => j.parent === request);
      return json(route, {
        request: { runId: request, sessionId: SESSION_ID, state: 'finished', status: 'completed', ask: ASK },
        total: mine.length,
        back: mine.length,
        jobs: mine.map((j) => ({
          runId: j.runId,
          title: j.goal,
          engine: j.engine,
          state: j.status === 'failed' ? 'failed' : 'finished',
          status: j.status,
          createdAt: j.endedAt - j.durationMs,
          endedAt: j.endedAt,
          continuesRunId: null,
        })),
      });
    });
  }
  return seen;
}

const AXE_SOURCE = readFileSync(createRequire(__filename).resolve('axe-core/axe.min.js'), 'utf8');

async function axeViolations(page: Page, include: string[]): Promise<unknown[]> {
  if (!(await page.evaluate(() => 'axe' in window))) await page.evaluate(AXE_SOURCE);
  // Until the icon font draws a glyph its name is plain text, which axe weighs against the control's name.
  await page.evaluate(() => document.fonts.ready);
  return page.evaluate(async (selectors) => {
    type Result = { violations: Array<{ id: string; help: string; nodes: Array<{ target: string[] }> }> };
    const axe = (window as unknown as { axe: { run: (ctx: unknown, opts: unknown) => Promise<Result> } }).axe;
    const result = await axe.run({ include: selectors.map((s) => [s]) }, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }, resultTypes: ['violations'] });
    return result.violations.map((v) => ({ id: v.id, help: v.help, targets: v.nodes.slice(0, 5).map((n) => n.target.join(' ')) }));
  }, include);
}

async function shoot(page: Page, name: string, target?: string): Promise<void> {
  if (!SHOTS) return;
  if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity));
  if (target) await page.locator(target).first().scrollIntoViewIfNeeded();
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

test.describe('Chat — finished jobs report back in finish order (BGUI-6)', () => {
  for (const l of LOOKS) {
    test(`reports land in finish order, honest about how each ended; a row opens the job (${l.name})`, async ({ page }) => {
      const phone = l.viewport.width < 500;
      const seen = await mockBackend(page);
      await look(page, l);
      await page.goto(`/chat/?conversationId=${SESSION_ID}`);

      const first = page.locator('[data-testid="turn-jobs"]').first();
      await expect(first.getByTestId('job-card')).toHaveCount(3);
      const reports = first.getByTestId('job-report');
      // Finish order, from one read of the request: the export, then the failed PDF, then the partial notes.
      await expect(reports).toHaveCount(3);
      await expect.poll(async () => reports.evaluateAll((els) => els.map((e) => e.getAttribute('data-run-id')))).toEqual(['job-csv', 'job-pdf', 'job-notes']);
      expect(seen.requestReads).toBeGreaterThan(0);
      await expect(reports.nth(0).getByTestId('job-report-byline')).toHaveText('Export the Q3 invoices · finished in 18s');
      await expect(reports.nth(1).getByTestId('job-report-outcome')).toHaveText(" · couldn't finish");
      await expect(reports.nth(2).getByTestId('job-report-partial')).toHaveText('Partial');
      await expect(reports.nth(2).getByTestId('job-report-cut')).toHaveText('Stopped early: spending limit reached');
      // BGUI-11: no report shows a job's own words; what it delivered is the answer's own file card.
      await expect(first.getByText('the cost section is missing', { exact: false })).toHaveCount(0);
      const notes = reports.nth(2).getByTestId('artifact-card');
      await expect(notes).toContainText('board-notes.md');
      await expect(notes.getByRole('button', { name: 'Download artifact' })).toBeVisible();

      const later = page.locator('[data-testid="turn-jobs"]').nth(1);
      const own = later.getByTestId('job-report');
      await expect(own).toHaveCount(1);
      // One job says how it ended, never its reply (the chat's answer sums it up once).
      await expect(own).not.toContainText('legacy billing template');
      await expect(own.getByRole('button', { name: '2 working files' })).toBeVisible();
      if (phone) {
        const box = await own.getByRole('button', { name: 'Check the EU invoices', exact: true }).boundingBox();
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
        const row = await reports.nth(0).getByRole('button', { name: 'Download artifact' }).boundingBox();
        expect(row?.height ?? 0).toBeGreaterThanOrEqual(44);
        const working = await own.getByRole('button', { name: '2 working files' }).boundingBox();
        expect(working?.height ?? 0).toBeGreaterThanOrEqual(44);
      }
      expect(await axeViolations(page, ['[data-testid="job-reports"]'])).toEqual([]);
      await shoot(page, `01-several-${l.name}`, '[data-testid="turn-jobs"]');
      await later.getByTestId('job-reports').scrollIntoViewIfNeeded();
      await shoot(page, `02-one-${l.name}`);

      await own.getByRole('button', { name: '2 working files' }).click();
      await expect(page).toHaveURL(/job=job-eu/);
      await expect(page).toHaveURL(/section=outputs/);
      await shoot(page, `03-working-opens-outputs-${l.name}`);
    });
  }

  for (const l of LOOKS) {
    test(`BGUI-12: the job's reply names each cited source and opens the answer's popover (${l.name})`, async ({ page }) => {
      await mockBackend(page);
      await look(page, l);
      await page.goto(`/chat/?conversationId=${SESSION_ID}&job=job-eu&section=brief`);
      const reply = page.getByTestId('activity-panel').getByTestId('job-panel-reply');
      await expect(reply).toContainText('legacy billing template', { timeout: 30_000 });
      // The stored source by its title; the engine's own link by its site; never a bare number.
      await expect(reply).toContainText(/dollars\.\s*Billing templates – Acme…\s*1/);
      await expect(reply).toContainText(/is right\.\s*ecb\.europa\s*2/);
      await expect(reply).not.toContainText('](http');
      expect(await axeViolations(page, ['[data-testid="job-panel-reply"]'])).toEqual([]);
      await reply.scrollIntoViewIfNeeded();
      await shoot(page, `12-reply-chips-${l.name}`);

      await reply.locator('.citation-number-circle').first().click();
      const popover = page.locator('[data-radix-popper-content-wrapper]').filter({ hasText: 'prints every amount in USD' });
      await expect(popover).toBeVisible();
      await expect(popover.getByRole('link', { name: /open in/i })).toHaveAttribute('href', BILLING_DOCS);
      await shoot(page, `13-reply-popover-${l.name}`);
      // One popover at a time, as in the chat's answer: the next chip's replaces it.
      await reply.locator('.citation-number-circle').nth(1).click();
      await expect(popover).toHaveCount(0);
      const own = page.locator('[data-radix-popper-content-wrapper]').filter({ hasText: 'ecb.europa' });
      await expect(own).toBeVisible();
      await expect(own.getByRole('link', { name: /open in/i })).toHaveAttribute('href', 'https://www.ecb.europa.eu/stats/exchange/eurofxref');
      await shoot(page, `14-reply-own-link-${l.name}`);
    });
  }

  for (const l of LOOKS) {
    test(`BGUI-13: the chat's own answer names each source it links, and its tabs count them (${l.name})`, async ({ page }) => {
      await mockBackend(page);
      await look(page, l);
      await page.goto(`/chat/?conversationId=${SESSION_ID}`);
      const answer = page.getByTestId('chat-answer').nth(1);
      await expect(answer).toContainText('I checked the EU invoices', { timeout: 30_000 });
      // The adopted citation by its title; the job's own link by its site, its URL kept; never a bare number.
      await expect(answer).toContainText(/dollars,\s*Billing templates – Acme…\s*1/);
      await expect(answer).toContainText(/is right\.\s*ecb\.europa\s*2/);
      await expect(answer).not.toContainText('](http');
      await expect(answer.locator(`a[href="${ECB}"]`).first()).toBeAttached();
      const tabs = page.getByRole('tablist', { name: 'Answer views' }).nth(1);
      await expect(tabs.getByRole('tab', { name: /sources/i })).toContainText('2');
      await expect(tabs.getByRole('tab', { name: /citation/i })).toContainText('2');
      expect(await axeViolations(page, ['[data-testid="chat-answer"]'])).toEqual([]);
      await answer.scrollIntoViewIfNeeded();
      await shoot(page, `15-answer-chips-${l.name}`);

      await answer.locator('.citation-number-circle').nth(1).click();
      const own = page.locator('[data-radix-popper-content-wrapper]').filter({ hasText: 'ecb.europa' });
      await expect(own).toBeVisible();
      await expect(own.getByRole('link', { name: /open in/i })).toHaveAttribute('href', ECB);
      await shoot(page, `16-answer-own-link-${l.name}`);
      await page.keyboard.press('Escape');

      const sources = tabs.getByRole('tab', { name: /sources/i });
      await sources.click();
      await expect(page.locator(`[id="${await sources.getAttribute('aria-controls')}"]`)).toContainText('ecb.europa');
      await shoot(page, `17-answer-sources-${l.name}`);
    });
  }
});
