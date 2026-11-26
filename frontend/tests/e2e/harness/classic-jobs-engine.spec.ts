/**
 * CJ-5, the engine journeys of the classic jobs: a code_task job in
 * the classic chat, on the real engine path (`run.sh durable`, the `harness-engine` project): a broker sandbox
 * running the lane's engine (E2E_ENGINE, opencode by default) on the scripted model, the scripted SaaS behind the tool gateway. Nothing is mocked; every turn is
 * typed into the page and reaches only the scripted model (`pinScriptedModel`).
 *
 * - CJ-E1: the card goes Starting → Working with a now line → Done with "Made report-….pdf"; Details opens the panel
 *   with Brief, Activity and Machine tabs, its Brief's outputs listing the file. §6: the card follows the job's own stream
 *   (no call before it, no timeline poll), the brief is read only when the panel opens, the card shows Done within
 *   500 ms of the stream's end and the panel paints within 300 ms of the click.
 * - CJ-E2 (engine): the job's write asks; the card reads Needs you and holds the ask; nothing runs until Allow.
 * - CJ-E3: Stop on a working job gives "Stopped by you" on the card, the panel header and the Jobs row, with no
 *   confirm; Stop on a job that ended meanwhile reads "It had already finished".
 * - CJ-E6: a chained command that reads `/skills/pdf/SKILL.md` shows as "Read the PDF skill" on the card's steps and
 *   in the panel; AGENTS.md and the tools index are not skills.
 * - CJ-E7 (engine part): axe over the card and the panel, light and dark.
 *
 * The first test checks the scenarios themselves on today's chat (API only), so phase 1 proves the scripts drive the
 * stack before the cards exist.
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import {
  engineApproval,
  engineCarried,
  ENGINE_CITED_PAGES,
  engineCited,
  ENGINE_REPORT_WORKING_FILES,
  engineReport,
  engineSkillRead,
  engineStoppable,
  WRITE_TOOL,
} from '../fixtures/classic-jobs-scenarios';
import { allowOnceButton, conversationPath, ScriptedModel, ScriptedSaas, type Lane } from './support/lane';
import {
  centre,
  ALREADY_FINISHED,
  escapeRegExp,
  allowAsk,
  BUDGETS,
  cardElapsed,
  cardNow,
  cardStatus,
  clickToPanelMs,
  DESKTOP,
  detailsButton,
  openDetails,
  engineLane,
  expectNoAxeViolations,
  frameToStatusMs,
  handedOffJob,
  installJobProbes,
  JOB_END_FRAMES,
  jobCard,
  jobPanel,
  jobRow,
  JOBS_URL,
  jobTimeline,
  jumpRow,
  jumpTo,
  LOOKS,
  panelBrief,
  panelCommand,
  panelOutputs,
  panelStatus,
  panelSteps,
  panelTerminal,
  pendingAsk,
  recordApiCalls,
  releaseUntil,
  runCalls,
  sendJobTurn,
  showCard,
  shoot,
  statusTrail,
  stepsToggle,
  stopGone,
  stopButton,
  STOPPED_BY_YOU,
  useLook,
  waitingCount,
} from './support/classic-jobs';

// Each test sends its own turn, so one failing leaves the others to run (one worker: they share the scripted model).

let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;
test.beforeAll(async () => {
  l = engineLane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
});
test.afterAll(async () => {
  await model?.close();
  await saas?.close();
});
test.beforeEach(async ({ page }) => {
  // Engine runs boot a sandbox: minutes, not seconds, on a loaded host.
  test.setTimeout(600_000);
  await model.reset();
  await saas.reset();
  await page.setViewportSize(DESKTOP);
  await installJobProbes(page);
});

const tag = (): string => randomBytes(3).toString('hex');
const status = async (page: Page, runId: string): Promise<string | null> => jobCard(page, runId).getAttribute('data-status');

test('CJ-5 scenarios (engine): a code_task job asks, is allowed, and makes its file; another is held until released', async ({ page, apiContext }) => {
  const asks = engineApproval(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, asks);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const ask = await pendingAsk(apiContext, agentKey, conversationId, jobRunId);
  expect(await saas.effects(), 'nothing runs while the job waits').toEqual([]);
  await allowAsk(apiContext, agentKey, conversationId, jobRunId, ask);
  await releaseUntil(model, async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status === 'completed', 'the turn completes');
  expect((await saas.effects()).map((e) => e.tool)).toEqual([WRITE_TOOL]);
  await expect(page.getByText(asks.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });

  const report = engineReport(tag());
  const second = await sendJobTurn(page, l, report);
  const job = await handedOffJob(apiContext, second.agentKey, second.conversationId);
  await expect.poll(() => model.holding(), { message: 'the job is held working', timeout: 240_000 }).toBe(true);
  expect((await jobTimeline(apiContext, second.agentKey, second.conversationId, job.jobRunId)).status).toBe('running');
  await releaseUntil(model, async () => (await jobTimeline(apiContext, second.agentKey, second.conversationId, job.turnRunId)).status === 'completed', 'the turn completes');
  await expect(page.getByText(report.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
});

test('CJ-E1: a code_task card goes Starting, Working, Done with its file; Details opens Brief, Activity, Machine', async ({ page, apiContext }) => {
  const calls = recordApiCalls(page);
  const scenario = engineReport(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const card = jobCard(page, jobRunId);

  await expect(card).toHaveAttribute('data-status', 'working', { timeout: 240_000 });
  await expect(card).toHaveRole('group');
  await expect(card).toHaveAttribute('aria-label', new RegExp(`^${escapeRegExp(scenario.goal)}[\\s\\S]*, Working$`));
  await expect(cardStatus(card)).toHaveText('Working');
  await expect(cardNow(card)).not.toBeEmpty({ timeout: 60_000 });
  await expect(cardNow(card)).not.toContainText(/[{}]|"tool"|runId/);
  await expect(cardElapsed(card)).toHaveText(/^\d+s$|^\d+m \d{2}s$/);
  await expect(card).not.toContainText(/\$|USD/);
  await shoot(page, 'e1-working-desktop-light');

  // §6: the card opened the job's own stream first, from the run id on the hand-off; nothing else named the job yet.
  await expect.poll(() => runCalls(calls, jobRunId)[0] ?? null, { message: "the job's stream was opened" }).toBe('GET /runs/:id/stream');
  expect(runCalls(calls, jobRunId).filter((c) => /\/brief|\/activity/.test(c)), 'no brief or activity read before the panel opens').toEqual([]);

  await releaseUntil(model, async () => (await status(page, jobRunId)) === 'done', 'the card reads Done');
  await expect(cardStatus(card)).toHaveText('Done');
  await expect(cardNow(card)).toHaveText(`Made ${scenario.file}`);
  await expect(page.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  const trail = await statusTrail(page, jobRunId);
  // F6: a status never goes back a step.
  expect(trail.slice(trail.indexOf('working')), `the card's statuses: ${trail.join(' → ')}`).toEqual(['working', 'done']);
  expect(trail.slice(0, trail.indexOf('working')).every((s) => s === 'upNext'), 'only Starting comes before Working').toBe(true);
  expect(await frameToStatusMs(page, jobRunId, JOB_END_FRAMES, 'done'), 'Done within 500 ms of the frame').toBeLessThanOrEqual(BUDGETS.frameToCardMs);
  await shoot(page, 'e1-done-desktop-light');

  // BGUI-11: the report under the card says how it ended and what it delivered, never the engine's reply (the
  // chat's answer says it once); its working files are one line, counted by the server, that opens Outputs.
  const report = page.locator(`[data-testid="job-report"][data-run-id="${jobRunId}"]`);
  await expect(report.getByTestId('job-report-outcome')).toHaveText(/finished/, { timeout: 60_000 });
  await expect(report).not.toContainText(scenario.answer);
  await expect(report).not.toContainText('::artifact');
  await expect(report.getByRole('button', { name: `${ENGINE_REPORT_WORKING_FILES.length} working files` })).toBeVisible({ timeout: 60_000 });

  // Details: the panel on the job, Brief first once it has ended; the brief is read now, once.
  const opened = await clickToPanelMs(page, card);
  expect(opened, 'the panel paints within 300 ms of the click').toBeLessThanOrEqual(BUDGETS.clickToPanelMs);
  await expect(page).toHaveURL(new RegExp(`[?&]job=${jobRunId}(&|$)`));
  await expect(panelStatus(page)).toHaveText(/^Done/);
  for (const section of ['Brief', 'Activity', 'Outputs', 'Machine'] as const) await expect(jumpTo(page, section)).toBeVisible();
  // §4.2: an ended job's panel opens on Brief, and its tab says so (BGUI-3: `aria-selected`).
  await expect(jumpRow(page).locator('[role="tab"][aria-selected="true"]')).toHaveCount(1);
  await expect(jumpTo(page, 'Brief')).toHaveAttribute('aria-selected', 'true');
  await expect(panelBrief(page)).toContainText(scenario.goal);
  // What it made is on Outputs (BGUI-11), as the answer's file card, its working files folded under it: the same
  // count as the report's line.
  await jumpTo(page, 'Outputs').click();
  await expect(jumpTo(page, 'Outputs')).toHaveAccessibleName('Outputs 1');
  await expect(panelOutputs(page).getByTestId('artifact-card')).toContainText(scenario.file!);
  const working = panelOutputs(page).getByRole('button', { name: `${ENGINE_REPORT_WORKING_FILES.length} working files` });
  await working.click();
  for (const path of ENGINE_REPORT_WORKING_FILES) await expect(panelOutputs(page).getByTestId('activity-file').filter({ hasText: path })).toHaveCount(1);
  await expect(panelOutputs(page).getByTestId('activity-file').filter({ hasText: scenario.file! })).toHaveCount(0);
  await shoot(page, 'e1-outputs-desktop-light');
  await jumpTo(page, 'Brief').click();
  // The command is the Machine's; Activity saying "Nothing has run yet" beside it would be wrong (F7).
  await expect(panelSteps(page)).not.toContainText('Nothing has run yet');
  await jumpTo(page, 'Machine').click();
  await expect(panelCommand(page)).toContainText('mkdir -p out notes');
  // The scenario's command prints nothing; every engine's empty result is no output step, which the Machine tab says
  // as "No output." whichever engine the lane runs (E2E_ENGINE; R25OPS-FU-2).
  await expect(panelTerminal(page)).toHaveCount(0);
  await expect(panelCommand(page)).toContainText('No output.');
  await shoot(page, 'e1-panel-desktop-light');
  expect(runCalls(calls, jobRunId).filter((c) => c.endsWith('/brief')), 'the brief is read once, on open').toHaveLength(1);
  expect(runCalls(calls, jobRunId).filter((c) => c.endsWith('/brief/history')), 'its history only when expanded').toEqual([]);

  // Esc closes it, takes `job` out of the URL and gives focus back to Details.
  await page.keyboard.press('Escape');
  await expect(jobPanel(page)).toBeHidden();
  await expect(page).not.toHaveURL(/[?&]job=/);
  await expect(detailsButton(card)).toBeFocused();

  // No 3 s timeline poll: at most one settle read of the job once it ended.
  expect(runCalls(calls, jobRunId).filter((c) => c.endsWith('/timeline')).length).toBeLessThanOrEqual(1);
  expect((await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status).toBe('completed');

  // CJ-E7 (engine): axe over the card and the panel, in light and dark, desktop and phone.
  const url = `${page.url()}${page.url().includes('?') ? '&' : '?'}job=${jobRunId}`;
  for (const look of LOOKS) {
    await page.goto(url);
    await useLook(page, look);
    await expect(jumpRow(page)).toBeVisible({ timeout: 30_000 });
    await expectNoAxeViolations(page, `the job card and panel, ${look.name}`, ['[data-testid="job-card"]', '[data-testid="activity-panel"]']);
    await shoot(page, `e1-panel-${look.name}`);
  }
});

test("BGUI-10: a hand-off that loaded the PDF skill first: the job's Brief is its task, what it started with by name, never the engine's prompt", async ({ page, apiContext }) => {
  const scenario = engineCarried(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  await releaseUntil(model, async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status === 'completed', 'the turn completes');
  await expect(page.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });

  // The brief API: the parts the worker kept on the child's admission, the carried call by its tool alone.
  const res = await apiContext.get(`${conversationPath(agentKey, conversationId)}/runs/${jobRunId}/brief`);
  expect(res.ok(), `brief of ${jobRunId}: ${res.status()}`).toBe(true);
  const brief = (await res.json()) as { goal: string; task: { request: string; notes: string; carried: Array<Record<string, unknown>> } | null };
  expect(brief.task, 'the brief carries its task in parts').not.toBeNull();
  expect(brief.task!.notes).toBe(scenario.notes);
  expect(brief.task!.request).toContain(scenario.goal);
  expect(brief.task!.carried).toEqual([{ tool: 'load_skill', failed: false, skill: 'pdf' }]);
  // The engine itself was told the call and its result.
  expect(brief.goal).toContain('Tool calls this chat already made before handing over');

  const card = jobCard(page, jobRunId);
  await openDetails(card);
  await expect(jumpTo(page, 'Brief')).toHaveAttribute('aria-selected', 'true');
  const shown = panelBrief(page);
  await expect(jobPanel(page).getByTestId('job-brief-goal')).toHaveText(scenario.notes);
  await expect(jobPanel(page).getByTestId('job-brief-asked')).toContainText(scenario.goal);
  // The typed turn ("You asked") holds the scripted model's lines; nothing the harness wrote for the engine shows.
  for (const leaked of ['Notes from the chat agent', 'Tool calls this chat', 'time and money', 'already made before']) {
    await expect(shown).not.toContainText(leaked);
  }
  await shoot(page, 'bgui10-brief-desktop-light');
  await jobPanel(page).getByRole('button', { name: 'What it started with' }).click();
  await expect(jobPanel(page).getByTestId('job-brief-started').locator('li')).toHaveText([/Loaded the PDF skill$/]);
  await shoot(page, 'bgui10-brief-started-desktop-light');
});

test("BGUI-12: an engine job's reply in its panel names each page it cites, links it, and its timeline carries the answer's citations", async ({ page, apiContext }) => {
  const scenario = engineCited(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  await releaseUntil(model, async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status === 'completed', 'the turn completes');

  // Node's child timeline: its final text keeps the engine's numbered links, and its answer's citations ride beside
  // it (none stored here: no tool read these pages).
  const child = (await jobTimeline(apiContext, agentKey, conversationId, jobRunId)) as unknown as {
    parts: Array<{ type: string; isFinal?: boolean; content?: string }>;
    answer?: { citations: unknown[] };
  };
  expect(child.parts.find((p) => p.type === 'text' && p.isFinal)?.content).toContain(`[1](${ENGINE_CITED_PAGES[0].url})`);
  expect(child.answer, 'the timeline carries the answer').toEqual({ citations: [] });

  const card = jobCard(page, jobRunId);
  await expect.poll(() => status(page, jobRunId), { timeout: 120_000 }).toBe('done');
  await openDetails(card);
  await jumpTo(page, 'Brief').click();
  const reply = jobPanel(page).getByTestId('job-panel-reply');
  await expect(reply).toContainText('Purple Cap', { timeout: 60_000 });
  for (const [i, cited] of ENGINE_CITED_PAGES.entries()) {
    await expect(reply).toContainText(new RegExp(`${cited.site}\\s*${i + 1}`));
    await expect(reply.locator(`a[href="${cited.url}"]`).first()).toBeAttached();
  }
  await expect(reply).not.toContainText('](http');
  await shoot(page, 'bgui12-engine-reply-desktop-light');
  await reply.locator('.citation-number-circle').first().click();
  const popover = page.locator('[data-radix-popper-content-wrapper]').filter({ hasText: 'iplt20.com' });
  await expect(popover.getByRole('link', { name: /open in/i })).toHaveAttribute('href', ENGINE_CITED_PAGES[0].url);
  await shoot(page, 'bgui12-engine-popover-desktop-light');
});

test("BGUI-13: the chat's own answer, repeating an engine job's links, names each page and links it; its tabs count them", async ({ page, apiContext }) => {
  const scenario = engineCited(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  await releaseUntil(model, async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status === 'completed', 'the turn completes');

  // The saved answer as the engine lane leaves it: the engine's numbered links, repeated, and no stored citation
  // behind them (no tool read these pages, so the job had none for the parent to adopt).
  const res = await apiContext.get(conversationPath(agentKey, conversationId));
  expect(res.ok(), `conversation ${conversationId}: ${res.status()}`).toBe(true);
  const saved = ((await res.json()) as { conversation: { messages: Array<{ messageType: string; content: string; citations?: unknown[] }> } }).conversation;
  const reply = saved.messages.filter((m) => m.messageType === 'bot_response').at(-1);
  expect(reply?.content).toContain(`[1](${ENGINE_CITED_PAGES[0].url})`);
  expect(reply?.citations ?? []).toEqual([]);

  const answer = page.getByTestId('chat-answer').last();
  await expect(answer).toContainText('Purple Cap', { timeout: 60_000 });
  for (const [i, cited] of ENGINE_CITED_PAGES.entries()) {
    await expect(answer).toContainText(new RegExp(`${cited.site}\\s*${i + 1}`));
    await expect(answer.locator(`a[href="${cited.url}"]`).first()).toBeAttached();
  }
  await expect(answer).not.toContainText('](http');
  const tabs = page.getByRole('tablist', { name: 'Answer views' }).last();
  await expect(tabs.getByRole('tab', { name: /sources/i })).toContainText('2');
  await answer.locator('.citation-number-circle').first().click();
  const popover = page.locator('[data-radix-popper-content-wrapper]').filter({ hasText: 'iplt20.com' });
  await expect(popover.getByRole('link', { name: /open in/i })).toHaveAttribute('href', ENGINE_CITED_PAGES[0].url);
  await shoot(page, 'bgui13-answer-popover-desktop-light');
  await page.keyboard.press('Escape');

  for (const look of LOOKS) {
    await useLook(page, look);
    const shown = page.getByTestId('chat-answer').last();
    await expect(shown).toContainText(new RegExp(`${ENGINE_CITED_PAGES[1].site}\\s*2`), { timeout: 30_000 });
    await shown.scrollIntoViewIfNeeded();
    await shoot(page, `bgui13-answer-${look.name}`);
  }
});

test('CJ-E2 (engine): a job that asks reads Needs you, holds the ask on its card, and runs only once allowed', async ({ page, apiContext }) => {
  const scenario = engineApproval(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const card = jobCard(page, jobRunId);

  await expect(card).toHaveAttribute('data-status', 'needsYou', { timeout: 240_000 });
  await expect(cardStatus(card)).toHaveText('Needs you');
  await expect(cardNow(card)).toHaveText(/^Wants to /);
  // BGUI-4: the ask is answered in the card floating over the message box; the job's card points there.
  const float = page.getByTestId('job-ask-float');
  await expect(card.getByTestId('job-ask-pointer')).toBeVisible();
  // BGUI-9 (BGUI10-FU-1): the compact card is itself the region, named by what it asks; its action line says it in
  // plain words, and Details opens classic's whole card in place.
  await expect(float).toHaveRole('region');
  await expect(float).toHaveAccessibleName(/create[ _]ticket/i);
  await expect(float.getByTestId('job-ask-action')).toHaveText(/create ticket/i);
  await float.getByRole('button', { name: 'Details' }).click();
  const details = page.getByTestId('job-ask-details');
  await expect(details.getByTestId('approval-card')).toBeVisible();
  await expect(details).toContainText('What will be sent');
  await float.getByRole('button', { name: 'Hide details' }).click();
  await expect(details).toHaveCount(0);
  // The dot, the badge and the Needs you list are the loop spec's (the same marks for any job).
  await expect.poll(() => waitingCount(apiContext)).toBe(1);
  expect(await saas.effects(), 'nothing runs while the card waits').toEqual([]);
  await shoot(page, 'e2-engine-needs-you-desktop-light');

  await allowOnceButton(float, WRITE_TOOL).click();
  await expect.poll(async () => (await saas.effects()).map((e) => e.tool), { timeout: 120_000 }).toEqual([WRITE_TOOL]);
  await releaseUntil(model, async () => (await status(page, jobRunId)) === 'done', 'the card reads Done');
  await expect.poll(() => waitingCount(apiContext)).toBe(0);
  await expect(page.getByText(scenario.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
});

test('CJ-E3: Stop on a working job reads "Stopped by you" on the card, the panel and the Jobs row; on an ended one, "It had already finished"', async ({ page, apiContext }) => {
  const scenario = engineStoppable(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId, jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const card = jobCard(page, jobRunId);
  await expect(card).toHaveAttribute('data-status', 'working', { timeout: 240_000 });
  await expect.poll(() => model.holding(), { message: 'the job is held working', timeout: 240_000 }).toBe(true);

  // One click, no confirm (D11).
  // An engine job's title is its whole goal, so the name starts with the goal's first line.
  await expect(stopButton(card)).toHaveAccessibleName(new RegExp(`^Stop job: ${escapeRegExp(scenario.goal)}`));
  await centre(stopButton(card));
  await stopButton(card).click();
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  await expect(card).toHaveAttribute('data-status', 'stopped', { timeout: 60_000 });
  await expect(cardStatus(card)).toHaveText(STOPPED_BY_YOU);
  await expect(stopButton(card)).toHaveCount(0);
  // The turn answers once its job is stopped; the card is opened after that answer has settled around it.
  await expect
    .poll(async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status, { timeout: 120_000 })
    .not.toBe('running');
  expect(await frameToStatusMs(page, jobRunId, JOB_END_FRAMES, 'stopped'), 'Stopped within 500 ms of the frame').toBeLessThanOrEqual(BUDGETS.frameToCardMs);
  expect((await jobTimeline(apiContext, agentKey, conversationId, jobRunId)).status).toBe('cancelled');

  await openDetails(card);
  await expect(panelStatus(page)).toHaveText(new RegExp(`^${STOPPED_BY_YOU}`));
  await expect(stopButton(jobPanel(page))).toHaveCount(0);
  await shoot(page, 'e3-stopped-panel-desktop-light');

  await page.goto(JOBS_URL.all);
  await expect(jobRow(page, jobRunId)).toContainText(STOPPED_BY_YOU, { timeout: 30_000 });
  await expect(stopButton(jobRow(page, jobRunId))).toHaveCount(0);
  await shoot(page, 'e3-stopped-jobs-desktop-light');

  // A Stop that arrives after the job ended: the job finishes between the card's last frame and the click, so the
  // server's own 409 is what the card words. The click is held until the job is done; nothing about it is mocked.
  // A released hold is keyed by its place in the turn, the same in every engineStoppable turn: a reset re-arms it.
  await model.reset();
  const late = engineStoppable(tag());
  const second = await sendJobTurn(page, l, late);
  const job = await handedOffJob(apiContext, second.agentKey, second.conversationId);
  const lateCard = jobCard(page, job.jobRunId);
  await expect(lateCard).toHaveAttribute('data-status', 'working', { timeout: 240_000 });
  await expect.poll(() => model.holding(), { timeout: 240_000 }).toBe(true);
  const ended = async () => (await jobTimeline(apiContext, second.agentKey, second.conversationId, job.jobRunId)).status === 'completed';
  await page.route(`**/runs/${job.jobRunId}/cancel`, async (route) => {
    await releaseUntil(model, ended, 'the job ends before the Stop reaches the server');
    await route.continue();
  });
  const refused = page.waitForResponse((r) => r.url().endsWith(`/runs/${job.jobRunId}/cancel`));
  await stopButton(lateCard).click();
  expect((await refused).status()).toBe(409);
  await expect(stopGone(lateCard)).toHaveText(ALREADY_FINISHED);
  await expect(lateCard).toHaveAttribute('data-status', 'done', { timeout: 60_000 });
  // F9: the note is the job's, so the card the settled answer draws still says it.
  await expect(page.getByText(late.answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await expect(stopGone(jobCard(page, job.jobRunId))).toHaveText(ALREADY_FINISHED);
});

test('CJ-E6: a chained command that reads the PDF skill shows as "Read the PDF skill" on the card and in the panel', async ({ page, apiContext }) => {
  const scenario = engineSkillRead(tag());
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { jobRunId } = await handedOffJob(apiContext, agentKey, conversationId);
  const card = jobCard(page, jobRunId);
  await releaseUntil(model, async () => (await status(page, jobRunId)) === 'done', 'the card reads Done');

  await showCard(page, card);
  await stepsToggle(card).click();
  await expect(card.getByText('Read the PDF skill', { exact: true })).toHaveCount(1);
  // The chained command is behind its row (FIX-SKILLSHOW), one click away; AGENTS.md and the tools index are not
  // skill reads.
  await card.getByRole('button', { name: 'Read the PDF skill' }).click();
  await expect(card.getByTestId('engine-command-line')).toContainText(/cat \/pipeshub\/AGENTS\.md/);
  await expect(card.getByText(/^Read the .* skill$/)).toHaveCount(1);
  await shoot(page, 'e6-skill-card-desktop-light');

  await openDetails(card);
  // The panel words the command where it lists commands (its Machine tab), closed, its output one click away.
  await jumpTo(page, 'Machine').click();
  await expect(jobPanel(page).getByTestId('activity-skill-command')).toHaveCount(1);
  await expect(jobPanel(page).getByText('Read the PDF skill', { exact: true })).toHaveCount(1);
  await expect(jobPanel(page).getByText(/^Read the .* skill$/)).toHaveCount(1);
  await shoot(page, 'e6-skill-panel-desktop-light');
});
