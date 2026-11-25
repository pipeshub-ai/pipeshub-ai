/**
 * CJ-5, finding jobs across chats, on the harness lane
 * with loop sub-agent jobs:
 *
 * - CJ-E4: jobs in two chats. `/jobs/?view=all` lists one under Working and one under Finished; Open goes to the right
 *   chat with that job's panel open, and a reload keeps the panel (the URL holds it).
 * - Sidebar dots (D10): a chat whose job works shows the working mark, from the server's `harnessState` and not this
 *   tab's own stream; once it finishes unseen it shows the finished dot; opening the chat sends the seen mark once
 *   and the dot goes. Each mark has words for a screen reader.
 * - CJ-E5: at 390 px the card's Stop is an icon button named for the job, the panel is the bottom sheet, the menu
 *   button carries the amber dot with "1 thing needs you", and Jobs rows are 44 px tap targets.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { loopApproval, loopHeld, READ_TOOL, WRITE_TOOL } from '../fixtures/classic-jobs-scenarios';
import { adminApi } from '../helpers/labs-flag.helper';
import { lane, ScriptedModel, ScriptedSaas, type Lane } from './support/lane';
import {
  allowAsk,
  badgeLabel,
  chatDot,
  DESKTOP,
  openDetails,
  grantTools,
  handedOffJob,
  installJobProbes,
  jobCard,
  jobPanel,
  jobRow,
  JOBS_URL,
  jobsSection,
  jobsTab,
  jobTimeline,
  menuDot,
  openLink,
  panelHeader,
  pendingAsk,
  PHONE,
  recordApiCalls,
  releaseUntil,
  sendJobTurn,
  shoot,
  smallTouchTargets,
  stopButton,
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
  test.setTimeout(300_000);
  await model.reset();
  await saas.reset();
  await page.setViewportSize(DESKTOP);
  await installJobProbes(page);
});

const tag = (): string => randomBytes(3).toString('hex');
const chatUrl = (agentKey: string, conversationId: string) => `/chat/?agentId=${agentKey}&conversationId=${conversationId}`;

test('CJ-E4: jobs in two chats sit under Working and Finished; Open lands on the right chat with the panel; a reload keeps it', async ({ page, apiContext }) => {
  // The finished one first: the scripted model holds one call at a time, so only the second job is left held.
  const first = loopHeld(tag());
  const done = await sendJobTurn(page, l, first);
  const doneJob = await handedOffJob(apiContext, done.agentKey, done.conversationId);
  await releaseUntil(model, async () => (await jobTimeline(apiContext, done.agentKey, done.conversationId, doneJob.turnRunId)).status === 'completed', 'the first turn completes');

  // A released hold is keyed by its place in the turn, the same in every loopHeld turn: a reset re-arms it.
  await model.reset();
  const second = loopHeld(tag());
  const working = await sendJobTurn(page, l, second);
  const workingJob = await handedOffJob(apiContext, working.agentKey, working.conversationId);
  await expect.poll(() => model.holding(), { message: 'the second job is held working', timeout: 120_000 }).toBe(true);

  await page.goto(JOBS_URL.all);
  await expect(jobsTab(page, 'Jobs')).toHaveAttribute('aria-selected', 'true');
  await expect(jobsSection(page, 'Working').locator(`[data-run-id="${workingJob.jobRunId}"]`)).toBeVisible({ timeout: 30_000 });
  await expect(jobsSection(page, 'Finished').locator(`[data-run-id="${doneJob.jobRunId}"]`)).toBeVisible();
  await shoot(page, 'e4-jobs-desktop-light');

  await openLink(jobRow(page, doneJob.jobRunId)).click();
  await expect(page).toHaveURL(new RegExp(`conversationId=${done.conversationId}`));
  await expect(page).toHaveURL(new RegExp(`agentId=${done.agentKey}`));
  await expect(page).toHaveURL(new RegExp(`[?&]job=${doneJob.jobRunId}(&|$)`));
  await expect(panelHeader(page)).toBeVisible({ timeout: 30_000 });
  await expect(jobCard(page, doneJob.jobRunId)).toBeVisible();

  await page.reload();
  await expect(panelHeader(page)).toBeVisible({ timeout: 30_000 });
  await expect(jobCard(page, doneJob.jobRunId)).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`[?&]job=${doneJob.jobRunId}(&|$)`));
  await shoot(page, 'e4-panel-after-reload-desktop-light');

  await releaseUntil(model, async () => (await jobTimeline(apiContext, working.agentKey, working.conversationId, workingJob.turnRunId)).status === 'completed', 'the second turn completes');
});

test("Sidebar dots: working from the server's state, finished unseen, then cleared by one seen mark when the chat opens", async ({ page, apiContext }) => {
  const scenario = loopHeld(tag());
  const job = await sendJobTurn(page, l, scenario);
  const ids = await handedOffJob(apiContext, job.agentKey, job.conversationId);
  await expect.poll(() => model.holding(), { timeout: 120_000 }).toBe(true);

  // Another chat in a fresh tab: this tab never streamed the working one, so its mark can only come from the server.
  const other = await page.context().newPage();
  try {
    await other.setViewportSize(DESKTOP);
    await other.goto(`/chat/?agentId=${job.agentKey}`);
    await expect(chatDot(other, job.conversationId)).toHaveAttribute('data-dot', 'working', { timeout: 60_000 });
    await expect(chatDot(other, job.conversationId)).toContainText('Working');
    await shoot(other, 'dots-working-desktop-light');

    // The tab that has the chat open marks it seen when its run ends; with it closed, nobody has seen the finish.
    await page.close();
    await releaseUntil(model, async () => (await jobTimeline(apiContext, job.agentKey, job.conversationId, ids.turnRunId)).status === 'completed', 'the turn completes');
    // The dot follows a finished run without a reload: while a listed chat works the sidebar revalidates every 30 s (§6).
    await expect(chatDot(other, job.conversationId)).toHaveAttribute('data-dot', 'finished', { timeout: 60_000 });
    await expect(chatDot(other, job.conversationId)).toContainText('Finished since you last looked');
    await shoot(other, 'dots-finished-desktop-light');

    const calls = recordApiCalls(other);
    await other.goto(chatUrl(job.agentKey, job.conversationId));
    await expect(other.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    await expect(chatDot(other, job.conversationId)).toHaveCount(0, { timeout: 30_000 });
    expect(calls.filter((c) => c.method === 'PUT' && c.path.endsWith('/seen')), 'one seen mark per open').toHaveLength(1);
  } finally {
    await other.close();
  }
});

test('CJ-E5: at 390 px, an icon Stop named for the job, the bottom sheet, the menu button dot, 44 px Jobs rows', async ({ page, apiContext }) => {
  await page.setViewportSize(PHONE);
  const held = loopHeld(tag());
  const job = await sendJobTurn(page, l, held);
  const ids = await handedOffJob(apiContext, job.agentKey, job.conversationId);
  const card = jobCard(page, ids.jobRunId);
  await expect(card).toHaveAttribute('data-status', 'working', { timeout: 120_000 });
  await expect.poll(() => model.holding(), { timeout: 120_000 }).toBe(true);

  const stop = stopButton(card);
  await expect(stop).toHaveAccessibleName(/^Stop job: \S/);
  await expect(stop).not.toContainText('Stop');
  expect(await smallTouchTargets(card), 'every control on the card is a 44 px target').toEqual([]);
  // Full width of the answer column: nothing past the screen's edge, and no narrower than the column's text.
  const box = await card.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= PHONE.width, `the card fits the screen (${JSON.stringify(box)})`).toBe(true);
  expect(box && box.width >= 300, `the card spans the column (${box?.width} px)`).toBe(true);
  await shoot(page, 'e5-card-phone-light');

  await openDetails(card);
  await expect(page.getByRole('dialog').filter({ has: jobPanel(page) })).toBeVisible();
  await expect(panelHeader(page)).toBeVisible();
  await shoot(page, 'e5-sheet-phone-light');
  await page.keyboard.press('Escape');
  await releaseUntil(model, async () => (await jobTimeline(apiContext, job.agentKey, job.conversationId, ids.turnRunId)).status === 'completed', 'the turn completes');

  // Something waits: the collapsed sidebar's menu button says so.
  const asks = loopApproval(tag());
  const asking = await sendJobTurn(page, l, asks);
  const askingJob = await handedOffJob(apiContext, asking.agentKey, asking.conversationId);
  const ask = await pendingAsk(apiContext, asking.agentKey, asking.conversationId, askingJob.jobRunId);
  // Live, with no reload: the ask's count push lights the menu dot (ADR-INBOX-02, F1).
  await expect(menuDot(page)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: new RegExp(badgeLabel(1)) })).toBeVisible();
  await shoot(page, 'e5-menu-dot-phone-light');

  await page.goto(JOBS_URL.all);
  const row = jobRow(page, ids.jobRunId);
  await expect(row).toBeVisible({ timeout: 30_000 });
  const rowBox = await row.boundingBox();
  expect(rowBox && rowBox.height >= 44, `a Jobs row is a 44 px target (${rowBox?.height} px)`).toBe(true);
  expect(await smallTouchTargets(row)).toEqual([]);
  await shoot(page, 'e5-jobs-phone-light');

  await allowAsk(apiContext, asking.agentKey, asking.conversationId, askingJob.jobRunId, ask);
  await expect(menuDot(page)).toHaveCount(0, { timeout: 60_000 });
});

