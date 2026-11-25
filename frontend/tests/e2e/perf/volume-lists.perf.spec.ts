/**
 * QAFINAL-FU-VOL (R30-LAT): the classic list pages at hundreds of items, held to perf/volume-budgets.json (written
 * before the first measurement; SLO.md, "Volume"). Each page is loaded with `small` items and with `large`:
 *   - the large load makes at most `extraRequests` more API requests than the small one (0: no request per row);
 *   - the large load draws its rows within `readyMs` of navigation (EV-15's 2 s).
 * Every API is served here (support/served-app.ts), so the spec needs only the built app: no stack, no model.
 */
import type { Page, Route } from '@playwright/test';
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { json, recordApi, serveShell, tally, USER_ID } from './support/served-app';

interface PageBudget { title: string; small: number; large: number; extraRequests: number; readyMs: number }
const BUDGETS = (JSON.parse(readFileSync(path.join(__dirname, 'volume-budgets.json'), 'utf8')) as { pages: Record<string, PageBudget> }).pages;

const pad = (i: number): string => String(i).padStart(4, '0');
const hex24 = (prefix: string, i: number): string => `${prefix}${i.toString(16)}`.padEnd(24, '0').slice(0, 24);
const iso = (i: number): string => new Date(Date.UTC(2026, 10, 1) + i * 60_000).toISOString();

interface Load { requests: string[]; readyMs: number }

/** One served load: `serve(n)` serves the page's lists at `n` items; ready is `ready` on screen. */
async function load(page: Page, url: string, n: number, serve: (page: Page, n: number) => Promise<void>, ready: (page: Page) => Promise<void>, flags: Record<string, boolean> = {}): Promise<Load> {
  await serveShell(page, flags);
  await serve(page, n);
  const requests = recordApi(page);
  await page.goto(url);
  await ready(page);
  const readyMs = await page.evaluate(() => performance.now());
  // Requests the drawn rows send right after they mount count too.
  await page.waitForTimeout(500);
  return { requests: [...requests], readyMs };
}

async function holdVolume(browser: import('@playwright/test').Browser, name: string, url: string, serve: (page: Page, n: number) => Promise<void>, ready: (page: Page, n: number) => Promise<void>, flags: Record<string, boolean> = {}): Promise<void> {
  const budget = BUDGETS[name];
  const run = async (n: number): Promise<Load> => {
    const context = await browser.newContext();
    try {
      return await load(await context.newPage(), url, n, serve, (p) => ready(p, n), flags);
    } finally {
      await context.close();
    }
  };
  // A warm load first, so the large load is not also the first one to fetch the app's code.
  await run(budget.small);
  const small = await run(budget.small);
  const large = await run(budget.large);
  const detail = `small ${JSON.stringify(tally(small.requests))}; large ${JSON.stringify(tally(large.requests))}`;
  test.info().annotations.push({ type: 'volume', description: `${name}: requests ${small.requests.length} -> ${large.requests.length}, ready at ${budget.large} ${Math.round(large.readyMs)} ms` });
  console.log(`VOLUME ${name} small=${small.requests.length} large=${large.requests.length} readyMs=${Math.round(large.readyMs)} ${detail}`);
  expect.soft(large.requests.length - small.requests.length, `${budget.title}: extra requests at ${budget.large} items (${detail})`).toBeLessThanOrEqual(budget.extraRequests);
  expect.soft(large.readyMs, `${budget.title}: ready at ${budget.large} items`).toBeLessThanOrEqual(budget.readyMs);
}

// ---- agents ---------------------------------------------------------------------------------------------------

const agent = (i: number) => ({
  id: `agent-${pad(i)}`, _key: `agent-${pad(i)}`, _id: `agentInstances/agent-${pad(i)}`, name: `Volume agent ${pad(i)}`,
  description: 'An agent of the volume spec.', models: [], startMessage: '', systemPrompt: '', tags: [], isActive: true,
  isDeleted: false, createdAtTimestamp: 1, updatedAtTimestamp: 1, createdBy: USER_ID, access_type: 'INDIVIDUAL',
  user_role: 'OWNER', can_edit: true, can_delete: true, can_share: true, can_view: true, shareWithOrg: false,
});

async function serveAgents(page: Page, n: number): Promise<void> {
  await page.route((url) => url.pathname === '/api/v1/agents' || url.pathname === '/api/v1/agents/', (route: Route) => {
    const params = new URL(route.request().url()).searchParams;
    const pageNo = Number(params.get('page') ?? '1');
    const limit = Number(params.get('limit') ?? '20');
    const rows = Array.from({ length: n }, (_, i) => agent(i)).slice((pageNo - 1) * limit, pageNo * limit);
    const totalPages = Math.ceil(n / limit);
    return json(route, { success: true, agents: rows, pagination: { currentPage: pageNo, limit, totalItems: n, totalPages, hasNext: pageNo < totalPages, hasPrev: pageNo > 1 } });
  });
}

test("VOL-1: the chat sidebar's agents at hundreds of agents asks for one page, never one per agent", async ({ browser }) => {
  test.setTimeout(240_000);
  await holdVolume(browser, 'agents-sidebar', '/chat/', serveAgents, async (page) => {
    await expect(page.getByText('Volume agent 0000', { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  });
});

// ---- the builder of an agent with many grants -----------------------------------------------------------------

const KEY = 'agent-grants';
async function serveGrantedAgent(page: Page, n: number): Promise<void> {
  const toolsets = Array.from({ length: n }, (_, i) => ({
    _key: `ts-${pad(i)}`, name: `toolset_${pad(i)}`, displayName: `Toolset ${pad(i)}`, type: 'app', instanceId: `inst-${pad(i)}`, instanceName: `Toolset ${pad(i)}`,
    tools: [{ _key: `tool-${pad(i)}`, name: 'search', fullName: `toolset_${pad(i)}.search`, toolsetName: `toolset_${pad(i)}`, description: 'Search.' }],
  }));
  const knowledge = Array.from({ length: n }, (_, i) => ({ _key: `kn-${pad(i)}`, connectorId: `kb-${pad(i)}`, name: `Collection ${pad(i)}`, type: 'KB', filters: {} }));
  await page.route((url) => url.pathname.replace(/\/$/, '') === `/api/v1/agents/${KEY}`, (route) =>
    route.request().method() === 'GET'
      ? json(route, { status: 'success', agent: { ...agent(0), id: KEY, _key: KEY, name: 'Granted agent', toolsets, knowledge, models: [], versioning: { latestVersion: 1, unpublishedChanges: false } } })
      : route.fallback(),
  );
}

test('VOL-2: the builder of an agent holding hundreds of grants loads it once, never one request per grant', async ({ browser }) => {
  test.setTimeout(240_000);
  await holdVolume(browser, 'builder-grants', `/agents/edit/?agentKey=${KEY}`, serveGrantedAgent, async (page) => {
    await expect(page.getByPlaceholder('e.g. Support bot')).toHaveValue('Granted agent', { timeout: 30_000 });
  });
});

// ---- Jobs and Needs you ---------------------------------------------------------------------------------------

const jobRun = (i: number) => ({
  runId: `run-${pad(i)}`, sessionId: hex24('c', i), parentRunId: `parent-${pad(i)}`, job: true, agentKey: null, agentName: null,
  conversationTitle: `Chat ${pad(i)}`, engine: null, state: i % 3 === 0 ? 'working' : 'finished', status: i % 3 === 0 ? 'running' : 'completed',
  outcome: null, createdAt: 1_762_000_000_000 - i * 1000, updatedAt: 1_762_000_000_000 - i * 1000, endedAt: i % 3 === 0 ? null : 1_762_000_000_000 - i * 1000,
  title: `Volume job ${pad(i)}`,
});

async function serveJobs(page: Page, n: number): Promise<void> {
  await page.route('**/api/v1/agent-harness/runs?*', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const limit = Math.min(Number(params.get('limit') ?? '100'), 100);
    const from = Number(params.get('cursor') ?? '0');
    const rows = Array.from({ length: n }, (_, i) => jobRun(i)).slice(from, from + limit);
    return json(route, { runs: rows, nextCursor: from + limit < n ? String(from + limit) : null });
  });
}

test('VOL-3: Jobs at hundreds of jobs asks for one page and draws it, never one request per job', async ({ browser }) => {
  test.setTimeout(240_000);
  await holdVolume(browser, 'jobs', '/jobs/?view=all', serveJobs, async (page) => {
    await expect(page.getByTestId('job-row').first()).toBeVisible({ timeout: 30_000 });
  });
});

const waitingItem = (i: number) => ({
  id: `inbox-${pad(i)}`, kind: 'approval', severity: 'normal', waiting: true, unread: true, archived: false,
  title: `Approve volume step ${pad(i)}`, description: 'A write waits for you.', at: iso(i),
  pointer: { type: 'conversation', conversationId: hex24('d', i) }, link: `/chat/?conversationId=${hex24('d', i)}`, subjectVersion: 1, subject: {},
});

async function serveNeedsYou(page: Page, n: number): Promise<void> {
  await serveJobs(page, 0);
  await page.route('**/api/v1/agent-harness/inbox?*', (route) =>
    json(route, { items: Array.from({ length: n }, (_, i) => waitingItem(i)), counts: { unread: n, waiting: n, badge: n, paused: false, pausedUntil: null } }),
  );
}

test('VOL-4: Needs you with hundreds of waiting items asks for its feed once, never one request per item', async ({ browser }) => {
  test.setTimeout(240_000);
  await holdVolume(browser, 'needs-you', '/jobs/?view=needs-you', serveNeedsYou, async (page) => {
    await expect(page.getByTestId('needs-you-row').first()).toBeVisible({ timeout: 30_000 });
  });
});

// ---- admin pages ----------------------------------------------------------------------------------------------

/** Schedules over many agents (one agent per ten schedules), each pinned to a version behind the live one. */
async function serveSchedules(page: Page, n: number): Promise<void> {
  const agents = Math.max(1, Math.ceil(n / 10));
  await page.route('**/api/v1/agent-harness/schedules?*', (route) =>
    json(route, {
      schedules: Array.from({ length: n }, (_, i) => ({
        id: `sched-${pad(i)}`, name: `Volume schedule ${pad(i)}`, actorId: USER_ID, agentKey: `agent-${pad(i % agents)}`, agentVersion: 1,
        agentName: `Volume agent ${pad(i % agents)}`, agentLiveVersion: 2,
        trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'UTC', catchUp: 'skip' }, maxCostUsd: null, deadlineS: 600,
        status: 'active', statusReason: null, nextFireAt: iso(i), createdAt: iso(i), input: { query: 'Report.' }, overlap: 'skip', notify: ['in_app'],
      })),
    }),
  );
  // What a page that still names each agent itself would ask for.
  await page.route('**/api/v1/agents/*', (route) => json(route, { status: 'success', agent: agent(0) }));
  await page.route('**/api/v1/agents/*/versions*', (route) => json(route, { versions: [], latestVersion: 2 }));
}

test('VOL-5: Schedules over hundreds of schedules and many agents names them from its own answer, never one request per row', async ({ browser }) => {
  test.setTimeout(240_000);
  await holdVolume(browser, 'schedules', '/workspace/schedules/', serveSchedules, async (page) => {
    await expect(page.getByTestId('schedule-row').first()).toBeVisible({ timeout: 30_000 });
  }, { ENABLE_SCHEDULED_RUNS: true });
});

/** AI access's "Who can use agents" over many groups: the access section names the groups it holds. */
async function serveAiAccess(page: Page, n: number): Promise<void> {
  const groups = Array.from({ length: n }, (_, i) => hex24('f', i));
  await page.route('**/api/v1/control-plane/policy/org/current/access', (route) =>
    route.request().method() === 'GET'
      ? json(route, {
          value: { harness: groups, engines: {}, models: {} },
          revision: 1, ceilings: null, options: null, locked: false, deployment: null,
          names: { groups: Object.fromEntries(groups.map((id, i) => [id, `Group ${pad(i)}`])) },
        })
      : route.fallback(),
  );
  await page.route('**/api/v1/userGroups/*', (route) => json(route, { _id: 'g', name: 'Group' }));
}

test('VOL-7: AI access with many groups allowed to use agents names them from the section, never one request per group', async ({ browser }) => {
  test.setTimeout(240_000);
  await holdVolume(browser, 'ai-access', '/workspace/ai-access/', serveAiAccess, async (page) => {
    // The section drawn with its groups; a page that names each group itself sends those requests as the chips mount.
    await expect(page.getByText('Who can use agents', { exact: true })).toBeVisible({ timeout: 30_000 });
  });
});

// ---- the builder's catalogs (R30-LAT-FU-1) ----------------------------------------------------------------------

/**
 * The org's collections, connected apps and actions at `n` each, served the way their APIs page and search them:
 * collections by page (`limit` up to 100, `search` on the name), apps by cursor (`q`), actions by page (`search`).
 */
async function serveCatalogs(page: Page, n: number): Promise<void> {
  const collections = Array.from({ length: n }, (_, i) => ({ id: `kb-${pad(i)}`, name: `Volume collection ${pad(i)}`, connectorId: `kb-${pad(i)}` }));
  const apps = Array.from({ length: n }, (_, i) => ({
    id: `app-${pad(i)}`, name: `Volume drive ${pad(i)}`, nodeType: 'app', parentId: null, origin: 'CONNECTOR', connector: 'drive',
    recordType: null, recordGroupType: null, indexingStatus: null, reason: null, createdAt: 1, updatedAt: 1, sizeInBytes: null, mimeType: null,
    extension: null, webUrl: null, hasChildren: true, previewRenderable: null, permission: { role: 'OWNER' }, sharingStatus: 'team',
  }));
  const actions = Array.from({ length: n }, (_, i) => ({
    toolsetType: `volume_${pad(i)}`, displayName: `Volume action ${pad(i)}`, instanceId: `inst-${pad(i)}`, instanceName: `Volume action ${pad(i)}`,
    isConfigured: true, isAuthenticated: true, iconPath: '', category: 'app',
    tools: [{ name: 'search', fullName: `volume_${pad(i)}.search`, description: 'Search.' }],
  }));
  const matching = <T extends { name?: string; displayName?: string }>(rows: T[], query: string | null): T[] =>
    query ? rows.filter((row) => (row.name ?? row.displayName ?? '').toLowerCase().includes(query.toLowerCase())) : rows;

  await page.route((url) => url.pathname.replace(/\/$/, '') === '/api/v1/knowledgeBase', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const limit = Math.min(Number(params.get('limit') ?? '20'), 100);
    const pageNo = Number(params.get('page') ?? '1');
    const rows = matching(collections, params.get('search'));
    const totalPages = Math.ceil(rows.length / limit);
    return json(route, {
      knowledgeBases: rows.slice((pageNo - 1) * limit, pageNo * limit),
      pagination: { page: pageNo, limit, totalCount: rows.length, totalPages, hasNext: pageNo < totalPages, hasPrev: pageNo > 1 },
    });
  });
  await page.route((url) => url.pathname === '/api/v1/knowledgeBase/knowledge-hub/nodes', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const limit = Number(params.get('limit') ?? '50');
    const from = Number(params.get('cursor') ?? '0');
    const rows = matching(apps, params.get('q'));
    return json(route, {
      success: true, error: null, id: null, currentNode: null, parentNode: null, items: rows.slice(from, from + limit),
      pagination: { nextCursor: from + limit < rows.length ? String(from + limit) : null, limit, totalItems: rows.length },
      filters: { applied: {}, available: {} },
    });
  });
  await page.route((url) => url.pathname === '/api/v1/toolsets/my-toolsets', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const limit = Math.min(Number(params.get('limit') ?? '20'), 200);
    const pageNo = Number(params.get('page') ?? '1');
    const rows = matching(actions, params.get('search'));
    const totalPages = Math.ceil(rows.length / limit);
    return json(route, {
      toolsets: rows.slice((pageNo - 1) * limit, pageNo * limit),
      pagination: { page: pageNo, limit, total: rows.length, totalPages, hasNext: pageNo < totalPages, hasPrev: pageNo > 1 },
      filterCounts: { all: rows.length, authenticated: rows.length, notAuthenticated: 0 },
    });
  });
}

test("VOL-8: the builder in an org with hundreds of collections, apps and actions asks for each catalog's first page, never the whole catalog", async ({ browser }) => {
  test.setTimeout(240_000);
  await holdVolume(browser, 'builder-catalogs', '/agents/new/', serveCatalogs, async (page) => {
    await expect(page.getByText('Volume collection 0000', { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  });
});

test('VOL-8b: the palette still finds a collection, an app and an action past the first page, by asking the server', async ({ page }) => {
  test.setTimeout(120_000);
  await serveShell(page);
  await serveCatalogs(page, 500);
  await page.goto('/agents/new/');
  await expect(page.getByText('Volume collection 0000', { exact: true }).first()).toBeVisible({ timeout: 30_000 });
  const search = page.getByPlaceholder(/search/i).first();
  for (const name of ['Volume collection 0450', 'Volume drive 0450', 'Volume action 0450']) {
    await search.fill(name);
    await expect(page.getByText(name, { exact: true }).first(), name).toBeVisible({ timeout: 10_000 });
  }
});

// ---- admin pages that name agents (R30-LAT-FU-2) --------------------------------------------------------------

