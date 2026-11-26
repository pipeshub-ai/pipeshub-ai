/**
 * CJ-5, the loop journeys of the classic jobs, on the harness lane
 * (`run.sh harness`): the tool agent's turn in deep mode hands its work to one loop sub-agent, a job like an engine
 * child (`kind=job` lists any run with a parent), with the scripted SaaS's tools. No engine, no sandbox.
 *
 * - A loop sub-agent's card: Working with a now line, then Done; Details opens the panel with Brief and Activity and
 *   no engine-only tab (D1: no Machine for a run with no machine).
 * - CJ-E2: the job's write asks. The card reads Needs you, the chat's sidebar row gets the amber dot, the badge reads
 *   1 (the server's own count, D7), `/jobs/?view=needs-you` lists the ask, "Review in chat" lands on the card with the
 *   ask in view, and Allow clears the badge and the dot.
 * - BG-2: a loop sub-agent's Stop either stops it ("Stopped by you") or is not shown; never a dead button.
 *
 * The first test checks the scenarios on today's chat (API only), so phase 1 proves the scripts drive the stack.
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { loopApproval, loopHeld, READ_TOOL, WRITE_TOOL } from '../fixtures/classic-jobs-scenarios';
import { adminApi } from '../helpers/labs-flag.helper';
import { allowOnceButton, lane, ScriptedModel, ScriptedSaas, type Lane } from './support/lane';
import {
  centre,
  allowAsk,
  escapeRegExp,
  badgeLabel,
  cardNow,
  cardStatus,
  chatDot,
  sidebarRow,
  DESKTOP,
  openDetails,
  grantTools,
  handedOffJob,
  installJobProbes,
  jobCard,
  jobPanel,
  jobsTab,
  jobTimeline,
  jumpTo,
  needsYouBadge,
  needsYouEntry,
  needsYouRows,
  panelBrief,
  panelStatus,
  panelSteps,
  pendingAsk,
  releaseUntil,
  sendJobTurn,
  shoot,
  stopButton,
  STOPPED_BY_YOU,
  waitingCount,
} from './support/classic-jobs';

/** The scripted SaaS's write as the approval card words it (UXINT-3), and so Needs you (ADR-INBOX-02 D3). */
const ASK_TITLE = 'Create ticket in E2Esaas?';

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
const status = async (page: Page, runId: string): Promise<string | null> => jobCard(page, runId).getAttribute('data-status');

test('CJ-5 scenarios (loop): a deep turn hands its work to a sub-agent job that asks and is allowed; another is held until released', async ({ page, apiContext }) => {
  const asks = loopApproval(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, asks);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const ask = await pendingAsk(apiContext, agentKey, conversationId, jobRunId);
  await expect.poll(() => waitingCount(apiContext), { message: "the server counts the job's ask" }).toBeGreaterThanOrEqual(1);
  expect(await saas.effects(), 'nothing runs while the job waits').toEqual([]);
  await allowAsk(apiContext, agentKey, conversationId, jobRunId, ask);
  await expect
    .poll(async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status, { timeout: 120_000 })
    .toBe('completed');
  expect((await saas.effects()).map((e) => e.tool)).toEqual([WRITE_TOOL]);
  await expect(page.getByText(asks.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });

  const held = loopHeld(tag());
  const second = await sendJobTurn(page, l, held);
  const job = await handedOffJob(apiContext, second.agentKey, second.conversationId);
  await expect.poll(() => model.holding(), { message: 'the job is held working', timeout: 120_000 }).toBe(true);
  expect((await jobTimeline(apiContext, second.agentKey, second.conversationId, job.jobRunId)).status).toBe('running');
  await releaseUntil(model, async () => (await jobTimeline(apiContext, second.agentKey, second.conversationId, job.turnRunId)).status === 'completed', 'the turn completes');
  await expect(page.getByText(held.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
});

test("CJ-5 loop job card: Working with a now line, then Done; Details shows Brief and Activity and no machine's sections", async ({ page, apiContext }) => {
  const scenario = loopHeld(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const card = jobCard(page, jobRunId);

  await expect(card).toHaveAttribute('data-status', 'working', { timeout: 120_000 });
  await expect.poll(() => model.holding(), { timeout: 120_000 }).toBe(true);
  await expect(cardStatus(card)).toHaveText('Working');
  // F10 (D15): named by its goal, as the Jobs page names it; never its role id.
  await expect(card).toHaveAttribute('aria-label', `${scenario.goal}, Working`);
  await expect(card).not.toContainText('pipeshub-subagent');
  // Held after its tool lookup, which the chat never names, a loop job may have no now line yet; never a raw id.
  await expect(card).not.toContainText(new RegExp(`${READ_TOOL}|search_tools|[{}]`));
  await shoot(page, 'loop-working-desktop-light');

  await releaseUntil(model, async () => (await status(page, jobRunId)) === 'done', 'the card reads Done');
  await expect(cardStatus(card)).toHaveText('Done');
  // The answer settles (and its steps fold) before the card is opened, so the click does not race the re-render.
  await expect(page.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await openDetails(card);
  await expect(panelStatus(page)).toHaveText(/^Done/);
  await expect(jobPanel(page).getByTestId('job-panel-title')).toHaveText(scenario.goal);
  await expect(jumpTo(page, 'Brief')).toBeVisible();
  await expect(jumpTo(page, 'Activity')).toBeVisible();
  await expect(jumpTo(page, 'Machine')).toHaveCount(0);
  await expect(panelBrief(page)).toContainText(scenario.goal);
  await jumpTo(page, 'Activity').click();
  await expect(panelSteps(page)).toContainText(/release/i);
  await expect(panelSteps(page)).not.toContainText('search_tools');
  await shoot(page, 'loop-panel-desktop-light');
});

test('CJ-E2: a job that asks reads Needs you; the dot, the badge and the Needs you list follow it; Review in chat; Allow clears them', async ({ page, apiContext }) => {
  const scenario = loopApproval(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const ask = await pendingAsk(apiContext, agentKey, conversationId, jobRunId);
  const card = jobCard(page, jobRunId);

  await expect(card).toHaveAttribute('data-status', 'needsYou', { timeout: 120_000 });
  await expect(cardStatus(card)).toHaveText('Needs you');
  await expect(cardNow(card)).toHaveText(/^Wants to /);
  // BGUI-4: a job's ask is answered in the card floating over the message box; its card points there.
  const float = page.getByTestId('job-ask-float');
  await expect(card.getByTestId('job-ask-pointer')).toBeVisible();
  await expect(allowOnceButton(float, WRITE_TOOL)).toBeVisible();
  // The chat's row in another tab, which never streamed it: the mark comes from the server's state.
  const other = await page.context().newPage();
  await other.setViewportSize(DESKTOP);
  await other.goto(`/chat/?agentId=${agentKey}`);
  await expect(chatDot(other, conversationId)).toHaveAttribute('data-dot', 'needsYou', { timeout: 30_000 });
  await expect(chatDot(other, conversationId)).toContainText('Needs you');
  await shoot(other, 'e2-needs-you-dot-desktop-light');
  const waiting = await waitingCount(apiContext);
  expect(waiting, 'this ask is the only one waiting on the person').toBe(1);
  // The live count is its own test below; here the badge is read as a page that opened after the ask shows it.
  await other.reload();
  await expect(needsYouBadge(other)).toHaveAccessibleName(badgeLabel(waiting), { timeout: 30_000 });
  expect(await saas.effects(), 'nothing runs while the card waits').toEqual([]);
  await shoot(page, 'e2-needs-you-card-desktop-light');

  // The sidebar entry opens the page on Needs you, because something waits (D6).
  await needsYouEntry(page).click();
  await expect(page).toHaveURL(/\/jobs\/?\?view=needs-you/);
  await expect(jobsTab(page, 'Needs you')).toHaveAttribute('aria-selected', 'true');
  const row = needsYouRows(page).and(page.locator(`[data-interaction-id="${ask}"]`));
  await expect(row).toBeVisible({ timeout: 30_000 });
  // §4.4, words only: the row's title names the ask as the job card's approval does, never by the tool's id (F4).
  // Its meta line repeats the job's and the chat's titles, which here are the scripted message that names the tool.
  await expect(row.getByText(ASK_TITLE, { exact: true })).toBeVisible();
  await expect(row).not.toContainText(`E2Esaas · ${WRITE_TOOL}`);
  await expect(row).not.toContainText('Approve:');
  await shoot(page, 'e2-needs-you-list-desktop-light');

  // Review in chat: the chat, the job's panel and the ask, all in the URL (D9); the card is on screen with its ask.
  // The link names the ask; the chat takes `ask` out of the URL once it has brought the card into view.
  const review = row.getByRole('link', { name: 'Review in chat' });
  await expect(review).toHaveAttribute('href', new RegExp(`[?&]ask=${escapeRegExp(ask)}(&|$)`));
  await review.click();
  await expect(page).toHaveURL(new RegExp(`conversationId=${conversationId}`));
  await expect(page).toHaveURL(new RegExp(`[?&]job=${jobRunId}(&|$)`));
  await expect(card).toBeInViewport({ timeout: 30_000 });
  await expect(allowOnceButton(float, WRITE_TOOL)).toBeInViewport();

  await allowOnceButton(float, WRITE_TOOL).click();
  await expect.poll(async () => (await saas.effects()).map((e) => e.tool), { timeout: 60_000 }).toEqual([WRITE_TOOL]);
  await expect.poll(() => waitingCount(apiContext)).toBe(0);
  await expect(needsYouEntry(page)).toBeVisible();
  await expect(card).toHaveAttribute('data-status', 'done', { timeout: 60_000 });
  await expect(page.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await other.reload();
  await expect(sidebarRow(other, conversationId)).toBeVisible({ timeout: 30_000 });
  await expect(needsYouBadge(other)).toHaveCount(0);
  await expect(sidebarRow(other, conversationId).locator('[data-testid="chat-dot"][data-dot="needsYou"]')).toHaveCount(0);
  await other.close();
});

test("BG-2: a loop sub-agent's Stop stops it", async ({ page, apiContext }) => {
  const scenario = loopHeld(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const card = jobCard(page, jobRunId);
  await expect(card).toHaveAttribute('data-status', 'working', { timeout: 120_000 });
  await expect.poll(() => model.holding(), { timeout: 120_000 }).toBe(true);

  // CJ-2's BG-2 check: the cancel route takes a loop sub-agent's run id, so its card offers Stop.
  await expect(stopButton(card)).toBeVisible();
  await centre(stopButton(card));
  await stopButton(card).click();
  await expect(card).toHaveAttribute('data-status', 'stopped', { timeout: 60_000 });
  await expect(cardStatus(card)).toHaveText(STOPPED_BY_YOU);
  expect((await jobTimeline(apiContext, agentKey, conversationId, jobRunId)).status).toBe('cancelled');
  // The turn answers once its job is stopped; the card is opened after that answer has settled around it.
  await expect
    .poll(async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status, { timeout: 120_000 })
    .not.toBe('running');
  await openDetails(card);
  await expect(panelStatus(page)).toHaveText(new RegExp(`^${STOPPED_BY_YOU}`));
  await expect(jobPanel(page)).toBeVisible();
});

test('D10: the chat this tab is streaming shows Needs you on its row, over its spinner', async ({ page, apiContext }) => {
  const scenario = loopApproval(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const ask = await pendingAsk(apiContext, agentKey, conversationId, jobRunId);
  await expect(jobCard(page, jobRunId)).toHaveAttribute('data-status', 'needsYou', { timeout: 120_000 });
  await expect(chatDot(page, conversationId)).toHaveAttribute('data-dot', 'needsYou', { timeout: 30_000 });
  await allowAsk(apiContext, agentKey, conversationId, jobRunId, ask);
});

test('Needs you is live (§4.4): the badge counts a job\'s new ask without a reload, and drops it once allowed', async ({ page, apiContext }) => {
  await page.goto(`/chat/?agentId=${l.toolAgentKey}`);
  await expect(needsYouEntry(page)).toBeVisible({ timeout: 30_000 });
  await expect(needsYouBadge(page)).toHaveCount(0);
  const scenario = loopApproval(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const ask = await pendingAsk(apiContext, agentKey, conversationId, jobRunId);
  expect(await waitingCount(apiContext)).toBe(1);
  await expect(needsYouBadge(page)).toHaveAccessibleName(badgeLabel(1), { timeout: 30_000 });
  await allowAsk(apiContext, agentKey, conversationId, jobRunId, ask);
  await expect(needsYouBadge(page)).toHaveCount(0, { timeout: 30_000 });
});
