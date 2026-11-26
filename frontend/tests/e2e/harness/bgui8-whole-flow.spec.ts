/**
 * BGUI-8: the background-agent screens together, on the harness lane's real stack (`run.sh harness`), on the
 * scripted model and SaaS.
 *
 * One turn in deep mode hands out three loop sub-agent jobs at once (the scripted coordinator's `job` lines,
 * `deployment/e2e/fake_llm/server.py` `_jobs`). The second files a ticket, which asks: the floating Needs-you card
 * answers it while its card points there. The other two are held working, and the person stops them out of order:
 * the last handed out from the chat's Background agents list, then the first from its card. So the reports land in
 * finish order, the reverse of the hand-out, under the cards and before the answer (the ticket, plainly done, has no
 * report: its card says Done). The list shows the three, a report opens its job's panel, and Permissions in this
 * chat shows a grant and removes it.
 *
 * Served by the spec, and only these: the chat's grants (`…/grants`). The seeded org's own rule asks for the ticket,
 * and an org rule is never granted for a chat, so no scripted tool can make a grant on this stack. Why loop jobs and
 * not engine jobs: `code_task` runs once per message (its description and refusal), so a turn hands out several jobs
 * only through deep mode's `spawn_agent`; a loop job makes no files, so the report's file row is left to
 * `chat/chat-job-reports.spec.ts`.
 *
 * At each step axe (WCAG 2.1 A and AA) holds over the whole page, in light and dark at
 * 1440 and 390 px, and each control the step shows is 44 px on the phone. The chat's responsiveness is measured while
 * the jobs work and once they reported (`responsiveness`). BGUI-9: the floating card is the design's compact card, measured
 * against the viewport (`expectCompact`), with classic's whole card under Details. With `E2E_UI_CASE_SHOTS=/work/pw/<dir>` it saves each step.
 */
import { randomBytes } from 'crypto';
import fs from 'node:fs';
import path from 'node:path';
import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { READ_TOOL, WRITE_TOOL } from '../fixtures/classic-jobs-scenarios';
import { adminApi } from '../helpers/labs-flag.helper';
import { allowOnceButton, lane, ScriptedModel, ScriptedSaas, type Lane } from './support/lane';
import {
  DESKTOP,
  expectNoAxeViolations,
  grantTools,
  installJobProbes,
  jobCard,
  jobPanel,
  jobTimeline,
  jumpTo,
  LOOKS,
  panelBrief,
  sendJobTurn,
  stopButton,
  smallTouchTargets,
  useLook,
  type Look,
} from './support/classic-jobs';

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
  test.setTimeout(900_000);
  await model.reset();
  await saas.reset();
  await page.setViewportSize(DESKTOP);
  await installJobProbes(page);
  await page.addInitScript(PERF_PROBE);
});

const discover = (...tools: string[]): string => `tool search_tools ${JSON.stringify({ query: tools.join(' '), make_visible: true })}`;
const jobLine = (goal: string, ...script: string[]): string => `job ${JSON.stringify({ goal, script })}`;

function wholeFlow(tag: string) {
  const jobs = {
    first: { goal: `Summarise the board notes ${tag}` },
    ticket: { goal: `File the quarter ticket ${tag}` },
    last: { goal: `Draft the investor update ${tag}` },
  };
  const answer = `The quarter ${tag} is closed.`;
  const held = (goal: string, topic: string) =>
    jobLine(goal, discover(READ_TOOL), 'hold', `tool ${READ_TOOL} ${JSON.stringify({ topic })}`, `say ${goal} is done.`);
  const prompt = [
    `Close the quarter ${tag}: summarise the board notes, file the ticket and draft the investor update.`,
    // A directive keeps the request word for word through the intent step; `usage` changes nothing else.
    'usage 1000',
    held(jobs.first.goal, 'board'),
    jobLine(
      jobs.ticket.goal,
      discover(READ_TOOL, WRITE_TOOL),
      `tool ${WRITE_TOOL} ${JSON.stringify({ title: `BGUI8 ${tag}`, body: 'From BGUI-8.' })}`,
      `say Ticket BGUI8 ${tag} is filed.`,
    ),
    held(jobs.last.goal, 'investors'),
    `answer ${answer}`,
  ].join('\n');
  return { jobs, answer, scenario: { stack: 'loop' as const, goal: jobs.first.goal, prompt, answer } };
}

/** The turn's jobs, in the order the coordinator handed them out (its children, oldest first). */
async function handedOut(page: Page, api: Parameters<typeof jobTimeline>[0], agentKey: string, conversationId: string): Promise<{ turnRunId: string; jobs: string[] }> {
  let turnRunId = '';
  let jobs: string[] = [];
  await expect
    .poll(
      async () => {
        if (!turnRunId) {
          const res = await api.get(`/api/v1/agents/${agentKey}/conversations/${conversationId}`);
          turnRunId = res.ok() ? (((await res.json()) as { conversation?: { activeRunId?: string } }).conversation?.activeRunId ?? '') : '';
        }
        if (turnRunId) jobs = (await jobTimeline(api, agentKey, conversationId, turnRunId)).children.map((c) => c.runId);
        return jobs.length;
      },
      { message: 'the turn hands out three jobs', timeout: 240_000 },
    )
    .toBe(3);
  await expect(jobCard(page).first()).toBeVisible({ timeout: 60_000 });
  return { turnRunId, jobs };
}

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.UI_CASE_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(dir, `bgui8-${name}.png`) });
}

/** axe over the whole page and, on the phone, 44 px for every control inside `scopes`. */
/** Every animation that ends has ended (spinners run for ever): a panel's slide, a toast's fade, read at its colour. */
async function settled(page: Page): Promise<void> {
  await page.evaluate(() =>
    Promise.all(document.getAnimations().filter((a) => a.effect?.getTiming().iterations !== Infinity).map((a) => a.finished.catch(() => undefined))),
  );
}

async function checkStep(page: Page, look: Look, step: string, scopes: Locator[]): Promise<void> {
  await settled(page);
  // Page-wide (BGUI8-FU-4): the classic chrome around the surfaces is held to the same rules.
  await expectNoAxeViolations(page, `${step}, ${look.name}`);
  // Every surface sits inside the viewport: nothing beside the chat runs off its right edge.
  for (const scope of scopes) {
    const box = await scope.boundingBox();
    if (!box) continue;
    expect(box.x, `${step}: starts on screen, ${look.name}`).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, `${step}: ends on screen, ${look.name}`).toBeLessThanOrEqual(look.viewport.width + 1);
  }
  if (look.viewport.width <= 428) {
    for (const scope of scopes) expect(await smallTouchTargets(scope), `${step}: controls under 44 px, ${look.name}`).toEqual([]);
  }
  await shot(page, `${step}-${look.name}`);
}

/**
 * The chat's responsiveness while the jobs draw, as a person types 40 characters into the composer and scrolls the
 * transcript: long tasks (over 50 ms on the main thread), the slowest input's handlers, and its whole time to the next
 * paint (Event Timing, INP's measure). Only the main thread's share is a budget: the Playwright image paints in
 * software, so the time to paint is compared with an empty chat's on the same page instead (`baseline`).
 */
const PERF_PROBE = () => {
  const w = window as unknown as { __bgui8: { long: number[]; events: Array<{ name: string; duration: number; processing: number }> } };
  w.__bgui8 = { long: [], events: [] };
  new PerformanceObserver((list) => list.getEntries().forEach((e) => w.__bgui8.long.push(e.duration))).observe({ type: 'longtask', buffered: true });
  new PerformanceObserver((list) => list.getEntries().forEach((e) => w.__bgui8.events.push({ name: e.name, duration: e.duration, processing: (e as PerformanceEventTiming).processingEnd - (e as PerformanceEventTiming).processingStart }))).observe({
    type: 'event',
    durationThreshold: 16,
    buffered: true,
  } as PerformanceObserverInit);
};
/** A key's handlers within one frame's budget at 20 fps, and no main-thread task past INP's "good" 200 ms. */
const HANDLER_BUDGET_MS = 50;
const LONG_TASK_BUDGET_MS = 200;

type Responsiveness = {
  step: string;
  longTasks: number;
  longTaskMs: number;
  worstLongTaskMs: number;
  worstHandlerMs: number;
  worstInputMs: number;
  slowest: string[];
};

async function responsiveness(page: Page, step: string): Promise<Responsiveness> {
  await page.evaluate(() => {
    const w = window as unknown as { __bgui8: { long: number[]; events: unknown[] } };
    w.__bgui8.long = [];
    w.__bgui8.events = [];
  });
  const composer = page.locator('textarea.chat-input-textarea');
  await composer.click();
  await page.keyboard.type('Is the quarter closed yet, and who filed it?', { delay: 25 });
  await page.mouse.move(700, 300);
  for (let i = 0; i < 6; i++) await page.mouse.wheel(0, i % 2 ? 400 : -400);
  await composer.fill('');
  await page.waitForTimeout(300);
  const raw = await page.evaluate(() => (window as unknown as { __bgui8: { long: number[]; events: Array<{ name: string; duration: number; processing: number }> } }).__bgui8);
  const result = {
    step,
    longTasks: raw.long.length,
    longTaskMs: Math.round(raw.long.reduce((a, b) => a + b, 0)),
    worstLongTaskMs: Math.round(Math.max(0, ...raw.long)),
    worstHandlerMs: Math.round(Math.max(0, ...raw.events.map((e) => e.processing))),
    worstInputMs: Math.round(Math.max(0, ...raw.events.map((e) => e.duration))),
    slowest: [...raw.events].sort((a, b) => b.duration - a.duration).slice(0, 4).map((e) => `${e.name} ${Math.round(e.duration)} ms (handlers ${Math.round(e.processing)} ms)`),
  };
  console.log(`BGUI8-PERF ${JSON.stringify(result)}`);
  expect(result.worstHandlerMs, `${step}: an input's handlers ran ${result.worstHandlerMs} ms`).toBeLessThanOrEqual(HANDLER_BUDGET_MS);
  expect(result.worstLongTaskMs, `${step}: a main-thread task ran ${result.worstLongTaskMs} ms`).toBeLessThanOrEqual(LONG_TASK_BUDGET_MS);
  return result;
}

/**
 * BGUI-9: the floating card stays the design's compact card: on a phone at most about 30% of the viewport, above the
 * message box and never over its input; on a desktop no taller than two lines of text and a row of actions.
 */
async function expectCompact(page: Page, look: Look, float: Locator): Promise<void> {
  const card = await float.boundingBox();
  const input = await page.locator('textarea.chat-input-textarea').boundingBox();
  expect(card, 'the floating card is on screen').not.toBeNull();
  expect(input, 'the message box is on screen').not.toBeNull();
  if (!card || !input) return;
  const share = (card.width * card.height) / (look.viewport.width * look.viewport.height);
  console.log(`BGUI9-COVER ${JSON.stringify({ look: look.name, height: Math.round(card.height), share: Math.round(share * 1000) / 1000 })}`);
  expect(card.y + card.height, `the card ends above the input, ${look.name}`).toBeLessThanOrEqual(input.y + 0.5);
  if (look.viewport.width <= 428) expect(share, `the card covers ${Math.round(share * 100)}% of the phone, ${look.name}`).toBeLessThanOrEqual(0.3);
  else expect(card.height, `the card is compact, ${look.name}`).toBeLessThanOrEqual(120);
}

const reportOrder = (page: Page): Promise<string[]> =>
  page.getByTestId('job-report').evaluateAll((els) => els.map((el) => el.getAttribute('data-run-id') ?? ''));

test('BGUI-8 whole flow: three jobs handed out, one asks in the floating card, two stopped out of order, reports in finish order, the list, the panel, a grant removed', async ({
  page,
  apiContext,
}) => {
  const { jobs, answer, scenario } = wholeFlow(randomBytes(3).toString('hex'));
  const { agentKey, conversationId } = await sendJobTurn(page, l, scenario);
  const { turnRunId, jobs: order } = await handedOut(page, apiContext, agentKey, conversationId);
  await expect(page.getByTestId('job-card')).toHaveCount(3, { timeout: 60_000 });
  const byGoal = async (goal: string): Promise<string> => {
    for (const runId of order) if ((await jobCard(page, runId).getAttribute('aria-label'))?.startsWith(goal)) return runId;
    throw new Error(`no card for ${goal}`);
  };
  const first = await byGoal(jobs.first.goal);
  const ticket = await byGoal(jobs.ticket.goal);
  const last = await byGoal(jobs.last.goal);
  expect(order, 'handed out in the plan order').toEqual([first, ticket, last]);
  const chat = page.url();

  // Step 1: the ticket asks in the floating card (its card points there); the other two are working.
  const float = page.getByTestId('job-ask-float');
  await expect(jobCard(page, ticket)).toHaveAttribute('data-status', 'needsYou', { timeout: 120_000 });
  await expect(jobCard(page, first)).toHaveAttribute('data-status', 'working', { timeout: 120_000 });
  await expect(jobCard(page, last)).toHaveAttribute('data-status', 'working', { timeout: 120_000 });
  await expect.poll(() => model.holding(), { message: 'the held jobs are working', timeout: 120_000 }).toBe(true);
  expect(await saas.effects(), 'nothing is filed while the ticket waits').toEqual([]);
  for (const look of LOOKS) {
    await useLook(page, look);
    await expect(float).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('job-ask-headline')).toContainText(jobs.ticket.goal);
    await expect(page.getByTestId('job-ask-pointer')).toHaveCount(1);
    // BGUI-9: the design's compact card: the action in plain words, Allow once against Keep it out, no whole card.
    await expect(float.getByTestId('job-ask-action')).toHaveText(/\?$/);
    await expect(allowOnceButton(float, WRITE_TOOL)).toBeVisible();
    await expect(float.getByRole('button', { name: /^Keep it out: / })).toBeVisible();
    await expect(float.getByTestId('approval-card')).toHaveCount(0);
    await checkStep(page, look, 'asking', [float]);
    await expectCompact(page, look, float);
    // Details: classic's whole card, in place on a desktop and a bottom sheet on a phone, with what will be sent.
    await float.getByRole('button', { name: 'Details' }).click();
    const details = page.getByTestId('job-ask-details');
    await expect(details.getByTestId('approval-card')).toBeVisible();
    await expect(details).toContainText('What will be sent');
    const sheet = page.getByRole('dialog');
    await checkStep(page, look, 'asking-details', [look.viewport.width <= 428 ? sheet : float]);
    if (look.viewport.width <= 428) await sheet.getByRole('button', { name: 'Close' }).click();
    else await float.getByRole('button', { name: 'Hide details' }).click();
    await expect(details).toHaveCount(0);
    await expect(float.getByRole('button', { name: 'Details' })).toBeFocused();
    await page.getByTestId('background-agents-button').click();
    const list = page.getByTestId('background-agents-panel');
    await expect(list.getByTestId('background-agents-row')).toHaveCount(3, { timeout: 30_000 });
    await checkStep(page, look, 'list-running', [list]);
    await page.keyboard.press('Escape');
    await expect(list).toHaveCount(0);
  }
  await useLook(page, LOOKS[0]!);

  const perf: Responsiveness[] = [await responsiveness(page, 'three jobs live, the floating card up')];

  // The floating card answers it. The seeded org's own rule asks for the ticket, and an org rule is never granted for
  // a chat (only the product's default for a named destination is, `app/harness/tool_rules.py`), so it is allowed once.
  await expect(float).toBeVisible({ timeout: 60_000 });
  await expect(float.getByTestId('allow-conversation')).toHaveCount(0);
  await allowOnceButton(float, WRITE_TOOL).click();
  await expect(float).toHaveCount(0, { timeout: 60_000 });
  await expect(jobCard(page, ticket)).toHaveAttribute('data-status', 'done', { timeout: 120_000 });
  await expect.poll(async () => (await saas.effects()).map((e) => e.tool), { timeout: 60_000 }).toEqual([WRITE_TOOL]);

  // The last handed out is stopped first, from the chat's list; then the first, from its card.
  await page.getByTestId('background-agents-button').click();
  const list = page.getByTestId('background-agents-panel');
  await list.locator(`[data-testid="background-agents-row"][data-run-id="${last}"]`).getByRole('button', { name: /^Stop / }).click();
  await expect(jobCard(page, last)).toHaveAttribute('data-status', 'stopped', { timeout: 60_000 });
  await page.keyboard.press('Escape');
  await expect(list).toHaveCount(0);
  await stopButton(jobCard(page, first)).click();
  await expect(jobCard(page, first)).toHaveAttribute('data-status', 'stopped', { timeout: 60_000 });
  await expect
    .poll(async () => (await jobTimeline(apiContext, agentKey, conversationId, turnRunId)).status, { message: 'the turn answers', timeout: 120_000 })
    .not.toBe('running');

  // No scripted tool can be granted for a chat on this stack (above), so the chat's grants are the one call served
  // here: a grant as Node lists it, and its removal, so the top bar's Permissions in this chat shares the right side
  // with the list and the panel on the real page.
  const grantsRoute = (url: URL) => url.pathname.includes(`/conversations/${conversationId}/grants`);
  const grant = { grantId: `cg_${'c'.repeat(40)}`, toolId: 'web@i/fetch_url', tool: { app: 'web', name: 'fetch_url' }, destination: 'onyx.app', sensitivity: 'public', grantedBy: 'me', grantedAt: new Date().toISOString() };
  let held = [grant];
  await page.route(grantsRoute, async (route) => {
    const body = (value: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) });
    if (route.request().method() === 'DELETE') {
      held = [];
      return body({ revoked: grant, runs: 0 });
    }
    return body({ grants: held });
  });

  // Step 2: the stopped two report back in the order they finished (the last handed out first), before the answer;
  // the ticket, done with nothing more to say, has its card only. The list shows the three; a report opens its panel.
  for (const look of LOOKS) {
    await page.goto(chat);
    await useLook(page, look);
    await expect.poll(() => reportOrder(page), { timeout: 60_000 }).toEqual([last, first]);
    const reports = page.getByTestId('job-report');
    await expect(reports.nth(0)).toContainText(jobs.last.goal);
    await expect(reports.nth(1)).toContainText(jobs.first.goal);
    await expect(page.getByTestId('job-reports')).toBeVisible();
    await checkStep(page, look, 'reports', [page.getByTestId('job-reports')]);

    await page.getByTestId('background-agents-button').click();
    await expect(list.getByTestId('background-agents-row')).toHaveCount(3, { timeout: 30_000 });
    for (const [runId, status] of [[ticket, 'done'], [first, 'stopped'], [last, 'stopped']] as const) {
      await expect(list.locator(`[data-testid="background-agents-row"][data-run-id="${runId}"]`)).toHaveAttribute('data-status', status);
    }
    await checkStep(page, look, 'list-finished', [list]);
    await page.keyboard.press('Escape');
    await expect(list).toHaveCount(0);

    await reports.nth(0).getByTestId('job-report-open').click();
    await expect(jobPanel(page)).toBeVisible({ timeout: 30_000 });
    await expect(jobPanel(page).getByTestId('job-panel-title')).toHaveText(jobs.last.goal, { timeout: 30_000 });
    await checkStep(page, look, 'panel', [jobPanel(page)]);
    await jumpTo(page, 'Brief').click();
    await expect(jumpTo(page, 'Brief')).toHaveAttribute('aria-selected', 'true');
    // Its own task, never the person's quoted request or the headers the coordinator wrapped it in.
    await expect(panelBrief(page)).toContainText(jobs.last.goal, { timeout: 30_000 });
    await expect(panelBrief(page)).not.toContainText('## ');
    await expect(panelBrief(page)).not.toContainText(jobs.first.goal);
    await checkStep(page, look, 'brief', [jobPanel(page)]);
    await page.keyboard.press('Escape');
    await expect(jobPanel(page)).toHaveCount(0);

    await page.getByTestId('chat-grants-button').click();
    const grants = page.getByTestId('chat-grants-panel');
    await expect(grants.getByTestId('chat-grant')).toHaveCount(1, { timeout: 30_000 });
    await checkStep(page, look, 'grants', [grants]);
    await page.keyboard.press('Escape');
    await expect(grants).toHaveCount(0);
  }
  await expect(page.getByText(answer, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await page.goto(chat);
  await useLook(page, LOOKS[0]!);
  await expect.poll(() => reportOrder(page), { timeout: 60_000 }).toEqual([last, first]);
  perf.push(await responsiveness(page, 'the board answered, two reports'));
  await page.goto(`/chat/?agentId=${l.toolAgentKey}`);
  await expect(page.locator('textarea.chat-input-textarea')).toBeVisible({ timeout: 30_000 });
  perf.push(await responsiveness(page, 'baseline: an empty chat'));
  await test.info().attach('bgui8-perf.json', { body: JSON.stringify(perf, null, 1), contentType: 'application/json' });

  await page.goto(chat);
  await useLook(page, LOOKS[0]!);
  await page.getByTestId('chat-grants-button').click();
  const grants = page.getByTestId('chat-grants-panel');
  await grants.getByRole('button', { name: /^Remove / }).click();
  await expect(grants.getByTestId('chat-grants-empty')).toBeVisible({ timeout: 30_000 });
  await settled(page);
  await expectNoAxeViolations(page, 'the grant removed');
  await shot(page, 'grant-removed-desktop-light');
});
