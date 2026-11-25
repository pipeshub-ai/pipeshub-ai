/**
 * R30-LAT (QAFINAL-FU-LAT): how long a person waits for the classic pages around the chat, in the browser on the
 * harness lane's stack, held to the budgets pre-registered in latency-budgets.json (SLO.md, "Browser latency"):
 *   - a page load: navigation to the page's own data drawn (the builder's form filled with the saved agent, Jobs,
 *     Needs you, Run settings, AI access, Schedules);
 *   - the builder's Save draft to the saved notice;
 *   - deleting a chat: the confirm click to its row gone from the sidebar, and for a chat whose run waits on an
 *     approval, to that run read as ended.
 * A load is timed on the page's own clock from navigation start (`performance.now()` is 0 there): ready is the first
 * DOM change after every list request the page needs has ended (Resource Timing) with the page's data selector
 * matched, so a spinner or an empty shell never counts. One warm-up load of each page is not counted.
 */
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, cancelLeftRuns, conversationPath, lane, script, ScriptedModel, ScriptedSaas, trackTurn, type Lane } from '../harness/support/lane';
import { composer, discover, openAgentChat, openDefaultChat } from './classic';
import { adminApi, setLabsFlag } from '../helpers/labs-flag.helper';
import { BUDGETS, recordSamples } from './support';

test.describe.configure({ mode: 'default' });

let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;
// Schedules is a Labs page: on for its load, then put back as it was.
const SCHEDULED_RUNS = 'ENABLE_SCHEDULED_RUNS';
let schedulesBefore = false;

test.beforeAll(async () => {
  l = lane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
  const admin = await adminApi();
  schedulesBefore = await setLabsFlag(admin, SCHEDULED_RUNS, true);
  await admin.dispose();
});
test.afterAll(async () => {
  await model.close();
  await saas.close();
  const admin = await adminApi();
  await setLabsFlag(admin, SCHEDULED_RUNS, schedulesBefore);
  await admin.dispose();
});
test.afterEach(async ({ apiContext }) => {
  await cancelLeftRuns(apiContext);
});

interface Ready {
  /** Every request whose URL matches one of these must have ended. */
  requests: string[];
  /** The page's data on screen. */
  selector: string;
  /** The selector's element is an input that holds a value (the builder's name). */
  filled?: boolean;
  /** Text the page shows once drawn, where no element of its own marks it (an empty list). */
  text?: string;
}

/** Registered before navigation: marks `window.__qaReady` at the first DOM change that finds the page ready. */
async function probeReady(page: Page, ready: Ready): Promise<void> {
  await page.addInitScript((r: Ready) => {
    const w = window as unknown as { __qaReady?: number };
    const patterns = r.requests.map((source) => new RegExp(source));
    const check = () => {
      if (w.__qaReady !== undefined) return;
      const ended = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
      if (!patterns.every((p) => ended.some((e) => p.test(e.name) && e.responseEnd > 0))) return;
      const el = document.querySelector(r.selector) as HTMLInputElement | null;
      if (!el || (r.filled && !el.value)) return;
      if (r.text !== undefined && !document.body.innerText.includes(r.text)) return;
      w.__qaReady = performance.now();
    };
    const start = () => {
      new MutationObserver(check).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true });
      // A response that changes nothing on screen still ends the wait at the next frame.
      const tick = () => {
        check();
        if (w.__qaReady === undefined) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    };
    if (document.documentElement) start();
    else document.addEventListener('readystatechange', start, { once: true });
  }, ready);
}

/** Navigation start to ready, on the page's clock, for each of `n` loads after one warm-up. */
async function timedLoads(page: Page, url: string, ready: Ready, n: number): Promise<number[]> {
  await probeReady(page, ready);
  const out: number[] = [];
  for (let i = 0; i <= n; i += 1) {
    await page.goto(url, { waitUntil: 'commit' });
    await expect.poll(() => page.evaluate(() => (window as unknown as { __qaReady?: number }).__qaReady ?? null), {
      message: `${url} drew its data`,
      timeout: 60_000,
    }).not.toBeNull();
    const at = await page.evaluate(() => (window as unknown as { __qaReady: number }).__qaReady);
    if (i > 0) out.push(at);
  }
  return out;
}

function holdToBudget(stats: Record<string, { p50: number; p95: number }>, samples: Record<string, number[]>): void {
  for (const [metric, { p50, p95 }] of Object.entries(stats)) {
    const budget = BUDGETS.metrics[metric];
    expect.soft(p50, `${metric} p50 (samples ${JSON.stringify(samples[metric])})`).toBeLessThanOrEqual(budget.p50_ms);
    expect.soft(p95, `${metric} p95`).toBeLessThanOrEqual(budget.p95_ms);
  }
}

const api = (path: string): string => String.raw`/api/v1/` + path;

test('QA-LAT-7: page loads: the builder, Jobs, Needs you, AI access and Schedules', async ({ page }) => {
  test.setTimeout(900_000);
  const loads: Record<string, [string, Ready]> = {
    builder_load_ms: [
      `/agents/edit/?agentKey=${l.agentKey}`,
      { requests: [api(String.raw`agents/${l.agentKey}(\?|$)`)], selector: 'input[placeholder="e.g. Support bot"]', filled: true },
    ],
    jobs_load_ms: ['/jobs/?view=all', { requests: [api(String.raw`agent-harness/runs\?kind=job`)], selector: '[data-testid="jobs-page"] h1' }],
    needs_you_load_ms: ['/jobs/?view=needs-you', { requests: [api(String.raw`agent-harness/inbox\?`)], selector: '[data-testid="jobs-page"] h1' }],
    ai_access_load_ms: ['/workspace/ai-access/', { requests: [api('agent-harness/model-accounts/coding-engine')], selector: 'h1, h2' }],
    schedules_load_ms: ['/workspace/schedules/', { requests: [api(String.raw`agent-harness/schedules\?scope=mine`)], selector: 'body', text: 'Your schedules' }],
  };
  const samples: Record<string, number[]> = {};
  for (const [metric, [url, ready]] of Object.entries(loads)) {
    // A fresh page per metric: each registers its own probe.
    const fresh = await page.context().newPage();
    samples[metric] = await timedLoads(fresh, url, ready, BUDGETS.metrics[metric].samples);
    await fresh.close();
  }
  holdToBudget(recordSamples(test.info(), 'pages', samples), samples);
});

test("QA-LAT-8: the builder's Save draft to the saved notice", async ({ page }) => {
  test.setTimeout(600_000);
  const n = BUDGETS.metrics.builder_save_ms.samples;
  const name = page.getByPlaceholder('e.g. Support bot');
  await page.goto(`/agents/edit/?agentKey=${l.agentKey}`);
  await expect(name).not.toHaveValue('', { timeout: 60_000 });
  const original = await name.inputValue();
  const saves: number[] = [];
  try {
    for (let i = 0; i <= n; i += 1) {
      await name.fill(i % 2 === 0 ? `${original} (QA)` : original);
      const save = page.getByRole('button', { name: 'Save draft', exact: true });
      await expect(save).toBeEnabled();
      await page.evaluate(() => {
        const w = window as unknown as { __qaSave: { t0?: number; done?: number }; __qaSaveObserver?: MutationObserver };
        w.__qaSaveObserver?.disconnect();
        w.__qaSave = {};
        document.addEventListener('click', (e) => {
          // The button's text holds its icon's ligature too.
          if ((e.target as Element | null)?.closest('button')?.textContent?.includes('Save draft') && w.__qaSave.t0 === undefined) w.__qaSave.t0 = performance.now();
        }, { capture: true, once: true });
        w.__qaSaveObserver = new MutationObserver(() => {
          if (w.__qaSave.t0 !== undefined && w.__qaSave.done === undefined && document.body.innerText.includes('Draft saved.')) w.__qaSave.done = performance.now();
        });
        w.__qaSaveObserver.observe(document.body, { subtree: true, childList: true, characterData: true });
      });
      await save.click();
      await expect.poll(() => page.evaluate(() => (window as unknown as { __qaSave: { done?: number } }).__qaSave.done ?? null), { timeout: 60_000 }).not.toBeNull();
      const m = await page.evaluate(() => (window as unknown as { __qaSave: { t0: number; done: number } }).__qaSave);
      if (i > 0) saves.push(m.done - m.t0);
      // The notice goes before the next save, so the next sample waits for its own.
      await expect(page.getByText(/^Draft saved\./)).toHaveCount(0, { timeout: 30_000 });
    }
  } finally {
    if ((await name.inputValue()) !== original) {
      await name.fill(original);
      await page.getByRole('button', { name: 'Save draft', exact: true }).click();
      await expect(page.getByText(/^Draft saved\./).first()).toBeVisible({ timeout: 30_000 });
    }
  }
  const samples = { builder_save_ms: saves };
  holdToBudget(recordSamples(test.info(), 'builder-save', samples), samples);
});

/** The chat's sidebar row on screen: the default chat's Recents and an agent's both start folded. */
async function showChat(page: Page, conversationId: string): Promise<void> {
  const row = page.locator(`a[href*="conversationId=${conversationId}"]`).first();
  await expect(async () => {
    if (!(await row.isVisible())) await page.getByText('Recents', { exact: true }).first().click();
    await expect(row).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
}

/** The sidebar row of a chat, its menu, Delete, the typed confirmation; resolves at the confirm click's page time. */
async function deleteFromSidebar(page: Page, conversationId: string): Promise<{ clickedAt: number; goneAt: number }> {
  await showChat(page, conversationId);
  const row = page.locator(`a[href*="conversationId=${conversationId}"]`).first();
  // The row's menu button is drawn only while the row is hovered; the row's innermost container holds it.
  const item: Locator = page.locator('div, li').filter({ has: row }).last();
  await row.hover();
  await item.getByRole('button', { name: 'More options' }).click();
  await page.getByRole('menuitem', { name: 'Delete', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByPlaceholder('DELETE').fill('DELETE');
  await page.evaluate((id) => {
    const w = window as unknown as { __qaDel: { t0?: number; gone?: number; wall?: number } };
    w.__qaDel = {};
    document.addEventListener('click', (e) => {
      const button = (e.target as Element | null)?.closest('[role="dialog"] button');
      if (button?.textContent?.trim().endsWith('Delete') && w.__qaDel.t0 === undefined) {
        w.__qaDel.t0 = performance.now();
        w.__qaDel.wall = Date.now();
      }
    }, { capture: true });
    const observer = new MutationObserver(() => {
      if (w.__qaDel.t0 !== undefined && !document.querySelector(`a[href*="conversationId=${id}"]`)) {
        w.__qaDel.gone = performance.now();
        observer.disconnect();
      }
    });
    observer.observe(document.body, { subtree: true, childList: true });
  }, conversationId);
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __qaDel: { gone?: number } }).__qaDel.gone ?? null), { timeout: 60_000 }).not.toBeNull();
  const m = await page.evaluate(() => (window as unknown as { __qaDel: { t0: number; gone: number; wall: number } }).__qaDel);
  return { clickedAt: m.wall, goneAt: m.gone - m.t0 };
}

/** The run's status in the person's own runs list (`GET /api/v1/agent-harness/runs`), or null when it is not listed. */
async function listedStatus(api: APIRequestContext, runId: string): Promise<string | null> {
  const res = await api.get('/api/v1/agent-harness/runs?limit=100');
  expect(res.ok(), `runs list: ${res.status()}`).toBe(true);
  const runs = ((await res.json()) as { runs: Array<{ runId: string; status: string }> }).runs;
  return runs.find((run) => run.runId === runId)?.status ?? null;
}

const ENDED = new Set(['completed', 'failed', 'cancelled']);

/** Polled until the run has ended (or is gone: only its erasure, which stops it first, removes it); when first seen so. */
async function runEndedAt(api: APIRequestContext, runId: string): Promise<number> {
  let at = 0;
  await expect.poll(async () => {
    const status = await listedStatus(api, runId);
    const ended = status === null || ENDED.has(status);
    if (ended) at = Date.now();
    return ended;
  }, { message: `run ${runId} ended after its chat was deleted`, timeout: 120_000, intervals: [50] }).toBe(true);
  return at;
}

test('QA-LAT-9: deleting a chat: its row leaves the sidebar, and a run waiting in it ends', async ({ page, apiContext }) => {
  test.setTimeout(900_000);
  await model.reset();
  await saas.reset();
  const n = BUDGETS.metrics.chat_delete_ms.samples;
  const gone: number[] = [];
  const stopped: number[] = [];
  for (let i = 0; i <= n; i += 1) {
    // A finished chat of the default chat.
    await openDefaultChat(page);
    await composer(page).fill(script(`QA delete sample ${i}`, `say Kept for deletion ${i}.`));
    await composer(page).press('Enter');
    await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
    const finished = new URL(page.url()).searchParams.get('conversationId') as string;
    await expect(page.getByText(`Kept for deletion ${i}.`, { exact: true })).toBeVisible({ timeout: 90_000 });
    await openDefaultChat(page);
    const plain = await deleteFromSidebar(page, finished);

    // A chat of the ticket agent whose run waits on an approval: deleting it must end the run.
    await openAgentChat(page, l.toolAgentKey);
    await composer(page).fill(script(`QA delete waiting ${i}`, discover('create_ticket'),
      `tool create_ticket ${JSON.stringify({ title: `QA-DEL ${i}`, body: 'Never filed.' })}`, 'say Filed.'));
    await composer(page).press('Enter');
    await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
    const waiting = new URL(page.url()).searchParams.get('conversationId') as string;
    // A sample that fails before its delete must not leave this run waiting in Needs you for the specs after it.
    trackTurn(l.toolAgentKey, waiting);
    await expect(allowOnceButton(page, 'create_ticket')).toBeVisible({ timeout: 90_000 });
    const conv = await apiContext.get(conversationPath(l.toolAgentKey, waiting));
    const runId = ((await conv.json()) as { conversation: { activeRunId: string } }).conversation.activeRunId;
    expect(runId, 'the waiting chat has its run').toBeTruthy();
    const before = await listedStatus(apiContext, runId);
    expect(before !== null && !ENDED.has(before), `the run is listed, not ended, before the delete (${String(before)})`).toBe(true);
    await openAgentChat(page, l.toolAgentKey);
    const held = await deleteFromSidebar(page, waiting);
    const endedAt = await runEndedAt(apiContext, runId);
    if (i === 0) continue;
    gone.push(plain.goneAt, held.goneAt);
    stopped.push(endedAt - held.clickedAt);
  }
  const samples = { chat_delete_ms: gone, delete_run_stopped_ms: stopped };
  holdToBudget(recordSamples(test.info(), 'delete', samples), samples);
  expect((await saas.effects()).length, 'no deleted chat filed its ticket').toBe(0);
});
