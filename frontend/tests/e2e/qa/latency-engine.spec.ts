/**
 * QA-CLASSIC (was QA-AUTO's new-interface one): how long a coding job takes to start, in the classic chat, on the
 * real engine path (`harness-engine`, run.sh durable: a broker sandbox running the lane's engine, E2E_ENGINE, on the
 * scripted model). Enter
 * in the message box to the job card reading Working, on the page's own clock; held to `job_start_ms` in
 * latency-budgets.json (SLO.md, "Browser latency"). Each job ends before the next starts, so a sample measures one
 * start, not a queue. Each engine's samples keep a file of their own (`latency-job-<engine>.json`); the report merges
 * them under the one budget, which a job start on any engine is held to (QAFINAL-FU-ENG).
 */
import { request as playwrightRequest } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { cancelLeftRuns, trackTurn } from '../harness/support/lane';
import { DEFAULT_SCRIPTED_ENGINE } from '../harness/support/scripted-model';
import { composer, jobCard, openAgentChat } from './classic';
import { BUDGETS, recordSamples } from './support';

test.describe.configure({ mode: 'serial' });

const AGENT_KEY = process.env.E2E_ENGINE_AGENT_KEY ?? '';
const LLM_URL = process.env.E2E_LLM_URL;
const CONTROL = process.env.E2E_LLM_CONTROL ?? '';
const ENGINE = process.env.E2E_CODING_ENGINE || DEFAULT_SCRIPTED_ENGINE;

test.afterEach(async ({ apiContext }) => {
  await cancelLeftRuns(apiContext);
});

test('QA-LAT-3: job start, Enter to the job card Working', async ({ page }) => {
  expect(AGENT_KEY && LLM_URL, 'needs the durable engine stack (deployment/e2e/run.sh durable)').toBeTruthy();
  test.setTimeout(900_000);
  const model = await playwrightRequest.newContext({ baseURL: LLM_URL, extraHTTPHeaders: { 'X-E2E-Control': CONTROL } });
  const samples: number[] = [];
  const n = BUDGETS.metrics.job_start_ms.samples;
  for (let i = 0; i < n; i += 1) {
    await openAgentChat(page, AGENT_KEY);
    await page.evaluate(() => {
      const w = window as unknown as { __qa: { t0?: number; working?: number } };
      w.__qa = {};
      document.addEventListener('keydown', (e) => { if (e.key === 'Enter' && w.__qa.t0 === undefined) w.__qa.t0 = performance.now(); }, { capture: true, once: true });
      new MutationObserver(() => {
        if (w.__qa.t0 !== undefined && w.__qa.working === undefined && document.querySelector('[data-testid="job-card"][data-status="working"]')) {
          w.__qa.working = performance.now();
        }
      }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-status'] });
    });
    await composer(page).fill([`Count the rows, sample ${i}.`, 'shell true', 'hold', `say Counted ${i}.`].join('\n'));
    await composer(page).press('Enter');
    await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
    trackTurn(AGENT_KEY, new URL(page.url()).searchParams.get('conversationId') as string);
    await expect.poll(() => page.evaluate(() => (window as unknown as { __qa: { working?: number } }).__qa.working ?? null), { timeout: 240_000 }).not.toBeNull();
    const m = await page.evaluate(() => (window as unknown as { __qa: { t0: number; working: number } }).__qa);
    samples.push(m.working - m.t0);
    // The job is held at its model call; each poll lets a held call go until the card reads Done.
    await expect
      .poll(async () => {
        if ((await jobCard(page).getAttribute('data-status')) === 'done') return true;
        if (((await (await model.get('/__control/holding')).json()) as { holding: boolean }).holding) await model.post('/__control/release');
        return false;
      }, { message: 'the job finishes', timeout: 240_000, intervals: [250, 500, 1_000] })
      .toBe(true);
  }
  await model.dispose();
  const stats = recordSamples(test.info(), `job-${ENGINE}`, { job_start_ms: samples });
  const budget = BUDGETS.metrics.job_start_ms;
  expect.soft(stats.job_start_ms.p50, `${ENGINE} job start p50 (samples ${JSON.stringify(samples)})`).toBeLessThanOrEqual(budget.p50_ms);
  expect.soft(stats.job_start_ms.p95, `${ENGINE} job start p95`).toBeLessThanOrEqual(budget.p95_ms);
});
