/**
 * CJ-E7, the accessibility pass and the performance budgets over
 * the classic jobs pages, on the harness lane with loop sub-agent jobs. The engine journeys hold the same budgets on
 * the engine lane (`classic-jobs-engine.spec.ts`).
 *
 * Budgets, each measured in the page on `performance.now()` (`support/classic-jobs.ts` probes):
 * - no round trip before a stream: the first call that names the job is its own stream, from the run id the hand-off
 *   already carries;
 * - the brief only on panel open, its history only when expanded, the panel's activity only once it is open;
 * - the card's status within 500 ms of the frame that changed it;
 * - the panel's first paint within 300 ms of the click, with the job's stream already followed;
 * - opening a chat adds no request beyond one `PUT …/seen`: the job's stream, at most one settle read of its timeline.
 *
 * axe (WCAG 2.1 A and AA) over the card with its ask, the panel, Jobs and Needs you, in light and dark, at 390 px and
 * 1440 px.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { loopApproval, loopHeld, READ_TOOL, WRITE_TOOL } from '../fixtures/classic-jobs-scenarios';
import { adminApi } from '../helpers/labs-flag.helper';
import { lane, ScriptedModel, ScriptedSaas, type Lane } from './support/lane';
import {
  allowAsk,
  briefHistoryToggle,
  BUDGETS,
  clickToPanelMs,
  DESKTOP,
  expectNoAxeViolations,
  frameToStatusMs,
  grantTools,
  handedOffJob,
  installJobProbes,
  JOB_END_FRAMES,
  jobCard,
  jobPanel,
  JOBS_PAGE_SELECTOR,
  JOBS_URL,
  jobTimeline,
  LOOKS,
  needsYouRows,
  panelBrief,
  pendingAsk,
  recordApiCalls,
  releaseUntil,
  runCalls,
  sendJobTurn,
  shoot,
  useLook,
} from './support/classic-jobs';

// Each test sends its own turn, so one failing leaves the others to run (one worker: they share the scripted model).

let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;
test.beforeAll(async () => {
  l = lane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
  const api = await adminApi();
  await grantTools(api, l.toolAgentKey, [READ_TOOL, WRITE_TOOL]);
  await api.dispose();
});
test.afterAll(async () => {
  await model.close();
  await saas.close();
});
test.beforeEach(async ({ page }) => {
  test.setTimeout(420_000);
  await model.reset();
  await saas.reset();
  await page.setViewportSize(DESKTOP);
  await installJobProbes(page);
});

const tag = (): string => randomBytes(3).toString('hex');

test('CJ-E7 perf: the stream first, the brief on open, Done within 500 ms, the panel within 300 ms, nothing extra on chat open', async ({ page, apiContext }) => {
  const calls = recordApiCalls(page);
  const scenario = loopHeld(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const card = jobCard(page, jobRunId);
  await expect(card).toHaveAttribute('data-status', 'working', { timeout: 120_000 });
  await expect.poll(() => model.holding(), { timeout: 120_000 }).toBe(true);

  await expect.poll(() => runCalls(calls, jobRunId)[0] ?? null, { message: "the job's stream is the first call that names it" }).toBe('GET /runs/:id/stream');
  expect(runCalls(calls, jobRunId).filter((c) => /\/brief|\/activity|\/timeline/.test(c)), 'nothing but the stream while the panel is shut').toEqual([]);

  await releaseUntil(model, async () => (await card.getAttribute('data-status')) === 'done', 'the card reads Done');
  const frameToCard = await frameToStatusMs(page, jobRunId, JOB_END_FRAMES, 'done');
  expect(frameToCard, `the card showed Done ${Math.round(frameToCard)} ms after the frame`).toBeLessThanOrEqual(BUDGETS.frameToCardMs);

  // The answer settles (its steps fold) before the click, so the click is the only thing the timing measures.
  await expect(page.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  const toPanel = await clickToPanelMs(page, card);
  expect(toPanel, `the panel painted ${Math.round(toPanel)} ms after the click`).toBeLessThanOrEqual(BUDGETS.clickToPanelMs);
  await expect(panelBrief(page)).toContainText(scenario.goal, { timeout: 30_000 });
  expect(runCalls(calls, jobRunId).filter((c) => c.endsWith('/brief')), 'the brief is read once, on open').toHaveLength(1);
  expect(runCalls(calls, jobRunId).filter((c) => c.endsWith('/brief/history')), 'no history until it is expanded').toEqual([]);
  await briefHistoryToggle(page).click();
  await expect.poll(() => runCalls(calls, jobRunId).filter((c) => c.endsWith('/brief/history')).length).toBe(1);
  await test.info().attach('cj5-perf.json', {
    body: JSON.stringify({ frameToCardMs: Math.round(frameToCard), clickToPanelMs: Math.round(toPanel) }),
    contentType: 'application/json',
  });

  // Opening the chat again: the job's stream (its journal replayed), at most one settle read, one seen mark.
  await page.keyboard.press('Escape');
  const before = calls.length;
  await page.reload();
  await expect(card).toHaveAttribute('data-status', 'done', { timeout: 60_000 });
  await expect(page.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  const reopened = calls.slice(before);
  const job = runCalls(reopened, jobRunId);
  expect(job.filter((c) => !/\/stream$|\/timeline$/.test(c)), `the job's calls on open: ${job.join(', ')}`).toEqual([]);
  expect(job.filter((c) => c.endsWith('/timeline')).length).toBeLessThanOrEqual(1);
  expect(reopened.filter((c) => c.method === 'PUT' && c.path.endsWith('/seen')), 'one seen mark per open').toHaveLength(1);
  // `POST /users/by-ids` is the chat's own read of the people it names, sent as a POST before this package.
  const writes = reopened.filter((c) => c.method !== 'GET' && !c.path.endsWith('/seen') && c.path !== '/api/v1/users/by-ids');
  expect(writes, 'opening a chat writes nothing else').toEqual([]);
  expect((await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status).toBe('completed');
});

test('CJ-E7 axe: the card with its ask, the panel, Jobs and Needs you, light and dark, 390 and 1440 px', async ({ page, apiContext }) => {
  const scenario = loopApproval(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const ask = await pendingAsk(apiContext, agentKey, conversationId, jobRunId);
  await expect(jobCard(page, jobRunId)).toHaveAttribute('data-status', 'needsYou', { timeout: 120_000 });
  const chat = page.url();
  const withPanel = `${chat}${chat.includes('?') ? '&' : '?'}job=${jobRunId}`;

  for (const look of LOOKS) {
    await page.goto(chat);
    await useLook(page, look);
    await expect(jobCard(page, jobRunId)).toHaveAttribute('data-status', 'needsYou', { timeout: 60_000 });
    await expectNoAxeViolations(page, `the job card with its ask, ${look.name}`, ['[data-testid="job-card"]']);
    await shoot(page, `e7-card-${look.name}`);

    await page.goto(withPanel);
    await expect(jobPanel(page)).toBeVisible({ timeout: 30_000 });
    await expectNoAxeViolations(page, `the job panel, ${look.name}`, ['[data-testid="activity-panel"]']);
    await shoot(page, `e7-panel-${look.name}`);

    await page.goto(JOBS_URL.needsYou);
    await expect(needsYouRows(page).first()).toBeVisible({ timeout: 30_000 });
    await expectNoAxeViolations(page, `Needs you, ${look.name}`, [JOBS_PAGE_SELECTOR]);
    await shoot(page, `e7-needs-you-${look.name}`);

    await page.goto(JOBS_URL.all);
    await expect(page.locator(`[data-run-id="${jobRunId}"]`).first()).toBeVisible({ timeout: 30_000 });
    await expectNoAxeViolations(page, `Jobs, ${look.name}`, [JOBS_PAGE_SELECTOR]);
    await shoot(page, `e7-jobs-${look.name}`);
  }

  await allowAsk(apiContext, agentKey, conversationId, jobRunId, ask);
  await expect.poll(async () => (await saas.effects()).map((e) => e.tool), { timeout: 60_000 }).toEqual([WRITE_TOOL]);
});
