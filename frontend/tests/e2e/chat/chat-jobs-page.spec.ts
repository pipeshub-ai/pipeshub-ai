/**
 * chat-jobs-page.spec.ts — CJ-4 (classic jobs plan §4.3, §4.4, D6, D7, D9, D10, D11, §9): the Jobs page, Needs you,
 * the sidebar's entries and run marks, and the phone menu dot, in a real browser.
 *
 * Every harness read is a page.route() mock in the shape Node serves (`person-runs`, `inbox-feed`, `inbox-counts`,
 * `harnessState` on the conversation list, `conversation-seen`). No turn is sent, so no model is reached; the
 * `setup` project signs in against the e2e stack. axe (WCAG 2.1 A and AA) runs over the page in every look.
 *
 * With UI_CASE_SHOTS=<dir>, each state saves its pictures there: <state>-<desktop|phone>-<light|dark>.png.
 */
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { MODEL_INFO, json } from './durable-conversation.mock';

const SHOTS = process.env.UI_CASE_SHOTS ?? '';
const NOW = Date.now();
const PAGE = { page: 1, limit: 30, totalCount: 3, totalPages: 1, hasNextPage: false, hasPrevPage: false };

const state = (over: Record<string, unknown>) => ({ activeRun: null, runState: null, needsYou: false, finishedUnseen: false, seenUpTo: null, ...over });
const conversation = (id: string, title: string, harnessState: Record<string, unknown>) => ({
  _id: id,
  userId: 'u1',
  orgId: 'o1',
  title,
  initiator: 'u1',
  createdAt: new Date(NOW - 3_600_000).toISOString(),
  updatedAt: new Date(NOW - 60_000).toISOString(),
  lastActivityAt: NOW - 60_000,
  isShared: false,
  isOwner: true,
  sharedWith: [],
  status: 'complete',
  harnessState,
});
const CONVERSATIONS = [
  conversation('conv-ask', 'Q3 report', state({ needsYou: true, runState: 'needs_you', activeRun: { runId: 'job-ask', status: 'waiting_approval' } })),
  conversation('conv-work', 'Fix flaky tests', state({ runState: 'working', activeRun: { runId: 'job-work', status: 'running' } })),
  conversation('conv-done', 'Onboarding notes', state({ finishedUnseen: true, seenUpTo: NOW - 7_200_000 })),
];

const personRun = (over: Record<string, unknown>) => ({
  runId: 'job-work',
  sessionId: 'conv-work',
  parentRunId: 'turn-1',
  job: true,
  agentKey: null,
  agentName: null,
  conversationTitle: 'Fix flaky tests',
  engine: 'claude_code',
  state: 'working',
  status: 'running',
  outcome: null,
  createdAt: NOW - 720_000,
  updatedAt: NOW,
  endedAt: null,
  title: 'Fix the flaky login test',
  ...over,
});
const RUNS = [
  personRun({ runId: 'job-ask', sessionId: 'conv-ask', conversationTitle: 'Q3 report', engine: 'codex', state: 'needs_you', status: 'waiting_approval', title: 'Build the quarterly PDF report', createdAt: NOW - 180_000 }),
  personRun({}),
  personRun({ runId: 'job-done', sessionId: 'conv-done', conversationTitle: 'Onboarding notes', engine: null, state: 'finished', status: 'completed', endedAt: NOW - 86_400_000, createdAt: NOW - 86_500_000, title: 'Summarise the contracts folder' }),
  personRun({ runId: 'job-stop', sessionId: 'conv-done', conversationTitle: 'Onboarding notes', engine: 'codex', state: 'stopped', status: 'cancelled', endedAt: NOW - 90_000_000, createdAt: NOW - 90_100_000, title: 'Migrate the CSV export' }),
];

const ASKS = [
  {
    id: 'a:job-ask:i1',
    kind: 'approval',
    severity: 'warning',
    waiting: true,
    unread: true,
    archived: false,
    title: 'Approve: Send email',
    description: 'A task is waiting for your decision before it goes on.',
    at: NOW - 180_000,
    pointer: { type: 'conversation', conversationId: 'conv-ask', runId: 'job-ask', interactionId: 'i1' },
    link: '/chat/?conversationId=conv-ask',
    subjectVersion: 1,
    subject: { action: { name: 'Send email', app: 'Gmail' } },
  },
  {
    id: 'q:turn-9:i9',
    kind: 'question',
    severity: 'info',
    waiting: true,
    unread: true,
    archived: false,
    title: 'A question for you',
    description: 'Which quarter should the report cover?',
    at: NOW - 1_200_000,
    pointer: { type: 'conversation', conversationId: 'conv-work', runId: 'turn-9', interactionId: 'i9' },
    link: '/chat/?conversationId=conv-work',
    subjectVersion: 1,
    subject: { question: 'Which quarter should the report cover?' },
  },
  {
    id: 's:cand-1',
    kind: 'skill_proposal',
    severity: 'info',
    waiting: true,
    unread: true,
    archived: false,
    title: 'New skill proposed: Invoice checks',
    description: 'Checks an invoice against its purchase order.',
    at: NOW - 3_600_000,
    pointer: { type: 'skill_candidate', candidateId: 'cand-1' },
    link: '/workspace/skills/personal/',
    subjectVersion: 1,
    subject: { skill: 'Invoice checks' },
  },
];

interface World {
  empty: boolean;
  cancels: string[];
  seen: number;
  feedFilters: string[];
}

async function mockHarness(page: Page, world: World): Promise<void> {
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    json(route, { featureFlags: {}, agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true } }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    json(route, { status: 'success', message: 'Success', models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }] }),
  );
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === '/api/v1/conversations',
    (route) =>
      route.request().method() === 'GET'
        ? json(route, {
            conversations: world.empty || new URL(route.request().url()).searchParams.get('source') === 'shared' ? [] : CONVERSATIONS,
            source: 'owned',
            pagination: world.empty ? { ...PAGE, totalCount: 0 } : PAGE,
          })
        : route.fallback(),
  );
  await page.route(/\/api\/v1\/conversations\/[^/]+\/seen$/, (route) => {
    world.seen += 1;
    return json(route, { seenUpTo: NOW });
  });
  await page.route(/\/api\/v1\/conversations\/[^/]+\/runs\/[^/]+\/cancel$/, (route) => {
    world.cancels.push(route.request().url());
    return json(route, { accepted: true });
  });
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === '/api/v1/agent-harness/runs',
    (route) => json(route, { runs: world.empty ? [] : RUNS, nextCursor: null }),
  );
  const counts = () => ({ unread: 0, waiting: world.empty ? 0 : ASKS.length, badge: world.empty ? 0 : ASKS.length, paused: false, pausedUntil: null });
  await page.route('**/api/v1/agent-harness/inbox/count', (route) => json(route, counts()));
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === '/api/v1/agent-harness/inbox',
    (route) => {
      world.feedFilters.push(new URL(route.request().url()).searchParams.get('filter') ?? 'all');
      return json(route, { items: world.empty ? [] : ASKS, counts: counts() });
    },
  );
}

const AXE_SOURCE = readFileSync(createRequire(__filename).resolve('axe-core/axe.min.js'), 'utf8');

async function axeViolations(page: Page, include: string): Promise<unknown[]> {
  if (!(await page.evaluate(() => 'axe' in window))) await page.evaluate(AXE_SOURCE);
  return page.evaluate(async (selector) => {
    type Result = { violations: Array<{ id: string; help: string; nodes: Array<{ target: string[] }> }> };
    const axe = (window as unknown as { axe: { run: (ctx: unknown, opts: unknown) => Promise<Result> } }).axe;
    const result = await axe.run({ include: [[selector]] }, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }, resultTypes: ['violations'] });
    return result.violations.map((v) => ({ id: v.id, help: v.help, targets: v.nodes.slice(0, 5).map((n) => n.target.join(' ')) }));
  }, include);
}

async function smallTargets(root: Locator): Promise<string[]> {
  return root.evaluate((scope) =>
    Array.from(scope.querySelectorAll<HTMLElement>('button, a[href], [role="tab"]'))
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (r.width + 0.5 < 44 || r.height + 0.5 < 44);
      })
      .map((el) => el.getAttribute('aria-label') || el.textContent?.trim() || el.tagName),
  );
}

async function shoot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity));
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}

const LOOKS = [
  { name: 'desktop-light', viewport: { width: 1440, height: 960 }, dark: false, phone: false },
  { name: 'desktop-dark', viewport: { width: 1440, height: 960 }, dark: true, phone: false },
  { name: 'phone-light', viewport: { width: 390, height: 844 }, dark: false, phone: true },
  { name: 'phone-dark', viewport: { width: 390, height: 844 }, dark: true, phone: true },
] as const;

async function look(page: Page, l: (typeof LOOKS)[number]): Promise<void> {
  await page.setViewportSize(l.viewport);
  await page.emulateMedia({ colorScheme: l.dark ? 'dark' : 'light' });
  await page.addInitScript((d) => localStorage.setItem('pipeshub-theme-preference', d ? 'dark' : 'light'), l.dark);
}

const world = (over: Partial<World> = {}): World => ({ empty: false, cancels: [], seen: 0, feedFilters: [], ...over });
const chatDot = (page: Page, id: string) => page.locator(`a[href*="conversationId=${id}"]`).first().getByTestId('chat-dot');
/** The main sidebar keeps Recents folded until asked. */
async function openRecents(page: Page): Promise<void> {
  const row = page.locator('a[href*="conversationId=conv-ask"]').first();
  if (!(await row.isVisible())) await page.getByText('Recents', { exact: true }).click();
  await expect(row).toBeVisible({ timeout: 30_000 });
}

test.describe('Classic jobs — the Jobs page, Needs you and the sidebar (CJ-4, needs the e2e stack)', () => {
  for (const l of LOOKS) {
    test(`sidebar: the Needs you badge, the run marks and the phone menu dot (${l.name})`, async ({ page }) => {
      await mockHarness(page, world());
      await look(page, l);
      await page.goto('/chat/');
      if (l.phone) {
        await expect(page.getByTestId('menu-needs-you-dot')).toBeVisible({ timeout: 30_000 });
        await expect(page.getByRole('button', { name: 'Open sidebar, 3 things need you' })).toBeVisible();
        await shoot(page, `01-menu-dot-${l.name}`);
        await page.getByRole('button', { name: /^Open sidebar/ }).click();
      }
      await expect(page.getByTestId('needs-you-badge')).toHaveAccessibleName('3 things need you', { timeout: 30_000 });
      await expect(page.getByRole('link', { name: /^Jobs/ })).toBeVisible();
      await openRecents(page);
      await expect(chatDot(page, 'conv-ask')).toHaveAttribute('data-dot', 'needsYou');
      await expect(chatDot(page, 'conv-ask')).toContainText('Needs you');
      await expect(chatDot(page, 'conv-work')).toHaveAttribute('data-dot', 'working');
      await expect(chatDot(page, 'conv-done')).toHaveAttribute('data-dot', 'finished');
      await expect(chatDot(page, 'conv-done')).toContainText('Finished since you last looked');
      await shoot(page, `02-sidebar-${l.name}`);
    });

    test(`Needs you: every ask, grouped, each opening its chat at the ask (${l.name})`, async ({ page }) => {
      const w = world();
      await mockHarness(page, w);
      await look(page, l);
      await page.goto('/jobs/?view=needs-you');
      await expect(page.getByRole('tab', { name: /^Needs you/ })).toHaveAttribute('aria-selected', 'true', { timeout: 30_000 });
      const rows = page.getByTestId('needs-you-row');
      await expect(rows).toHaveCount(3, { timeout: 30_000 });
      expect(w.feedFilters).toContain('waiting');
      const jobAsk = page.locator('[data-testid="needs-you-row"][data-interaction-id="i1"]');
      await expect(jobAsk).toContainText('Approve: Send email in Gmail');
      await expect(jobAsk).toContainText('Build the quarterly PDF report');
      await expect(jobAsk.getByRole('link', { name: 'Review in chat' })).toHaveAttribute('href', /conversationId=conv-ask&job=job-ask&ask=i1/);
      await expect(page.locator('[data-interaction-id="i9"]').getByRole('link', { name: 'Answer in chat' })).toHaveAttribute('href', /conversationId=conv-work&ask=i9$/);
      await expect(page.getByRole('region', { name: 'Reviews' }).getByRole('button', { name: 'Approve' })).toBeVisible();
      expect(await axeViolations(page, '[data-testid="jobs-page"]')).toEqual([]);
      if (l.phone) expect(await smallTargets(page.getByTestId('jobs-page'))).toEqual([]);
      await shoot(page, `03-needs-you-${l.name}`);
    });

    test(`Jobs: Working and Finished, Open into the chat, one-click Stop (${l.name})`, async ({ page }) => {
      const w = world();
      await mockHarness(page, w);
      await look(page, l);
      await page.goto('/jobs/?view=all');
      await expect(page.getByRole('tab', { name: /^Jobs/ })).toHaveAttribute('aria-selected', 'true', { timeout: 30_000 });
      const working = page.getByRole('region', { name: 'Working', exact: true });
      await expect(working.locator('[data-run-id="job-work"]')).toBeVisible({ timeout: 30_000 });
      await expect(page.getByRole('region', { name: 'Needs you', exact: true }).locator('[data-run-id="job-ask"]')).toBeVisible();
      const finished = page.getByRole('region', { name: 'Finished', exact: true });
      await expect(finished.locator('[data-run-id="job-done"]')).toBeVisible();
      await expect(finished.locator('[data-run-id="job-stop"]')).toContainText('Stopped by you');
      await expect(page.locator('[data-testid="job-row"][data-run-id="job-stop"]').getByRole('button', { name: /^Stop job: / })).toHaveCount(0);
      await expect(page.locator('[data-testid="job-row"][data-run-id="job-done"]').getByRole('link', { name: /Open/ })).toHaveAttribute('href', /conversationId=conv-done&job=job-done/);
      expect(await axeViolations(page, '[data-testid="jobs-page"]')).toEqual([]);
      if (l.phone) {
        const row = page.locator('[data-testid="job-row"][data-run-id="job-work"]');
        expect((await row.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
        expect(await smallTargets(page.getByTestId('jobs-page'))).toEqual([]);
      }
      await shoot(page, `04-jobs-${l.name}`);
      await page.getByRole('button', { name: 'Stop job: Fix the flaky login test' }).click();
      await expect.poll(() => w.cancels.length).toBe(1);
      expect(w.cancels[0]).toContain('/conversations/conv-work/runs/job-work/cancel');
    });

    test(`empty states: nothing needs you, no jobs yet (${l.name})`, async ({ page }) => {
      await mockHarness(page, world({ empty: true }));
      await look(page, l);
      await page.goto('/jobs/?view=needs-you');
      await expect(page.getByText('Nothing needs you right now.')).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId('needs-you-badge')).toHaveCount(0);
      await shoot(page, `05-needs-you-empty-${l.name}`);
      await page.getByRole('link', { name: 'See your jobs' }).click();
      await expect(page.getByText('No jobs yet. When an agent hands work to a background job, it shows here.')).toBeVisible();
      expect(await axeViolations(page, '[data-testid="jobs-page"]')).toEqual([]);
      await shoot(page, `06-jobs-empty-${l.name}`);
    });
  }

  test('opening a chat sends one seen mark, and its finished mark is gone', async ({ page }) => {
    const w = world();
    await mockHarness(page, w);
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.goto('/chat/?conversationId=conv-done');
    await expect.poll(() => w.seen, { timeout: 30_000 }).toBe(1);
    await openRecents(page);
    await expect(chatDot(page, 'conv-done')).toHaveCount(0);
    await expect(chatDot(page, 'conv-ask')).toHaveAttribute('data-dot', 'needsYou');
    expect(w.seen).toBe(1);
  });
});
