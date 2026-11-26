/**
 * CJ-5: what the classic jobs specs share.
 *
 * - **The page contract.** Every locator the specs use is here, built from the plan's own words and roles (§4, §5,
 *   §9), so a rename in CJ-2..CJ-4 is one edit in this file. The few test ids are the ones CJ-5 asks those packages
 *   to render (listed in `JOB_TEST_IDS`).
 * - **Turns.** A scenario (`fixtures/classic-jobs-scenarios.ts`) is typed into the page like any turn; the job is the
 *   turn's child run, read from the parent's timeline.
 * - **Probes.** What the browser received and when (`installJobProbes`): each frame of a job's own stream and each
 *   status the job card showed, both on `performance.now()`, so the §6 budgets are measured in the page and not
 *   through Playwright's polling.
 * - **Checks.** axe over the page (WCAG 2.1 AA), touch targets at phone width, and the pictures for the evidence.
 */
import { randomBytes } from 'node:crypto';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { expect, type APIRequestContext, type Browser, type Locator, type Page } from '@playwright/test';
import type { JobScenario } from '../../fixtures/classic-jobs-scenarios';
import { conversationPath, ScriptedModel, sendTurn, type Lane } from './lane';
import { pinScriptedModel, TURN_STREAM } from './scripted-model';
import { loginViaUI } from '../../helpers/login.helper';
import { deleteUserByEmail } from '../../helpers/members.helper';

// ---------------------------------------------------------------------------------------------------------------------
// The page contract.
// ---------------------------------------------------------------------------------------------------------------------

/** `JobStatus` (`lib/harness-jobs/job-status.ts`), the card's `data-status`. */
export type JobStatus = 'upNext' | 'working' | 'needsYou' | 'done' | 'couldntFinish' | 'stopped';
/** `ChatDot` (`lib/harness-jobs/jobs-list.ts`, D10), a sidebar row's `data-dot`. */
export type ChatDot = 'needsYou' | 'working' | 'finished';

/**
 * The test ids CJ-2..CJ-4 render (everything else is found by role and words):
 * - CJ-2: the job card `job-card` (`role="group"`, `data-status` a JobStatus, `data-run-id` the child run), its
 *   status word `job-card-status` ("Working", "Needs you", "Stopped by you"), the second part `job-card-now` (the now
 *   line, the ask, "Made …"), `job-card-elapsed`, `job-card-details`, the steps toggle `sub-agent-toggle` and
 *   `job-card-stop-gone` ("It had already finished").
 * - CJ-3: the panel's job header `job-panel-header` with `job-panel-status`, its sections `job-panel-brief`,
 *   `job-panel-outputs`, the terminal's output `activity-output` (`role="group"`, "Output of …"), the shared
 *   viewer's `job-panel-owner-only`, `job-brief-history-toggle`; the panel itself keeps `activity-panel`, its steps
 *   `activity-steps`.
 * - CJ-4: a sidebar chat row's mark `chat-dot` (`data-dot` a ChatDot), the Needs you badge `needs-you-badge`, the
 *   phone menu button's dot `menu-needs-you-dot`, the Jobs page's body `jobs-page`, its rows `job-row` (`data-status`, `data-run-id`) and
 *   `needs-you-row` (`data-run-id`, `data-interaction-id`).
 */
export const JOB_TEST_IDS = {
  card: 'job-card',
  cardStatus: 'job-card-status',
  cardNow: 'job-card-now',
  cardElapsed: 'job-card-elapsed',
  cardDetails: 'job-card-details',
  cardSteps: 'sub-agent-toggle',
  cardStopGone: 'job-card-stop-gone',
  panelOutput: 'activity-output',
  panelCommand: 'activity-command',
  panelOwnerOnly: 'job-panel-owner-only',
  briefHistory: 'job-brief-history-toggle',
  panel: 'activity-panel',
  panelSteps: 'activity-section-tools',
  panelHeader: 'job-panel-header',
  panelStatus: 'job-panel-status',
  panelBrief: 'job-panel-brief',
  panelOutputs: 'job-panel-outputs',
  chatDot: 'chat-dot',
  badge: 'needs-you-badge',
  menuDot: 'menu-needs-you-dot',
  jobsPage: 'jobs-page',
  jobRow: 'job-row',
  needsYouRow: 'needs-you-row',
} as const;

const T = JOB_TEST_IDS;

/** The job card of one child run; with no run id, the newest card on the page. */
export function jobCard(page: Page, runId?: string): Locator {
  return runId ? page.locator(`[data-testid="${T.card}"][data-run-id="${runId}"]`) : page.getByTestId(T.card).last();
}

export const cardStatus = (card: Locator): Locator => card.getByTestId(T.cardStatus);
export const cardNow = (card: Locator): Locator => card.getByTestId(T.cardNow);
export const cardElapsed = (card: Locator): Locator => card.getByTestId(T.cardElapsed);
export const detailsButton = (scope: Locator): Locator => scope.getByTestId(T.cardDetails);
export const stopGone = (card: Locator): Locator => card.getByTestId(T.cardStopGone);

/**
 * Brings a control to the middle of the view first: scrolled only "into view", a card's bottom row sits under the
 * composer, which then takes the click.
 */
export async function centre(target: Locator): Promise<void> {
  await target.evaluate((el) => el.scrollIntoView({ block: 'center' }));
}

/**
 * Keeps a job's card in sight: once the answer settles, the chat folds its steps (the card among them) under the
 * summary line, so each poll opens a folded summary until the card shows.
 */
export async function showCard(page: Page, card: Locator): Promise<void> {
  let last = '';
  await expect
    .poll(
      async () => {
        // A folding list still lays the card out while it clips it, so the summary's state decides, not the card's box.
        let folded = false;
        for (const summary of await page.getByTestId('activity-summary').all()) {
          if ((await summary.getAttribute('aria-expanded')) !== 'false') continue;
          folded = true;
          await summary.click();
        }
        if (folded || !(await card.isVisible())) return false;
        // And the card has stopped moving: the same box on two polls in a row (the answer has settled).
        const box = JSON.stringify(await card.boundingBox());
        const settled = box === last;
        last = box;
        return settled;
      },
      { message: "the job's card is in sight", timeout: 30_000, intervals: [250, 500, 1_000] },
    )
    .toBe(true);
}

/** Opens a job's panel from its card. */
export async function openDetails(card: Locator): Promise<void> {
  await showCard(card.page(), card);
  await centre(detailsButton(card));
  await detailsButton(card).click();
}
/** Stop is named with the job's title (§9): "Stop job: {title}". */
export const stopButton = (scope: Locator | Page): Locator => scope.getByRole('button', { name: /^Stop job: / });
/** The chevron's "{n} steps" toggle. */
export const stepsToggle = (card: Locator): Locator => card.getByTestId(T.cardSteps);

/** The panel opened on a job (the Activity panel in job mode, D4). */
export const jobPanel = (page: Page): Locator => page.getByTestId(T.panel);
export const panelHeader = (page: Page): Locator => jobPanel(page).getByTestId(T.panelHeader);
export const panelStatus = (page: Page): Locator => jobPanel(page).getByTestId(T.panelStatus);
/** The jump row is a `nav` of buttons with `aria-current` (§9). */
/** BGUI-3: the job panel's tabs (the design's Brief, Activity, Machine; BGUI-11's Outputs), one view at a time. */
export const jumpRow = (page: Page): Locator => jobPanel(page).getByRole('tablist', { name: 'Job sections' });
/** Outputs names its delivered files' count after its word ("Outputs 1"). */
export const jumpTo = (page: Page, section: 'Brief' | 'Activity' | 'Outputs' | 'Machine'): Locator =>
  jumpRow(page).getByRole('tab', { name: new RegExp(`^${section}( \\d+)?$`) });
export const panelBrief = (page: Page): Locator => jobPanel(page).getByTestId(T.panelBrief);
export const panelOutputs = (page: Page): Locator => jobPanel(page).getByTestId(T.panelOutputs);
/** The panel's Activity section, whatever draws its rows (tool steps for a loop job, engine steps for a code_task). */
export const panelSteps = (page: Page): Locator => jobPanel(page).getByTestId(T.panelSteps);
/** The terminal's output: a group named "Output of …" (CJ-3). */
export const panelTerminal = (page: Page): Locator => jobPanel(page).getByTestId(T.panelOutput).first();
/** One command the job ran, as its Machine tab lists it (line, exit, duration, output). */
export const panelCommand = (page: Page): Locator => jobPanel(page).getByTestId(T.panelCommand).first();
export const panelOwnerOnly = (page: Page): Locator => jobPanel(page).getByTestId(T.panelOwnerOnly);
export const briefHistoryToggle = (page: Page): Locator => jobPanel(page).getByTestId(T.briefHistory);
/** A Jobs row's Open link, named for the job ("Open job: {title}"). */
export const openLink = (row: Locator): Locator => row.getByRole('link', { name: /^Open job: / });

/** A chat's row in the sidebar: its link names the conversation (`buildChatHref`). */
export const sidebarRow = (page: Page, conversationId: string): Locator =>
  page.locator(`a[href*="conversationId=${conversationId}"]`).first();
export const chatDot = (page: Page, conversationId: string): Locator => sidebarRow(page, conversationId).getByTestId(T.chatDot);
/** The sidebar's two entries under New chat / Search (D6). */
export const needsYouEntry = (page: Page): Locator => page.getByRole('link', { name: /^Needs you/ });
export const jobsEntry = (page: Page): Locator => page.getByRole('link', { name: /^Jobs/ });
export const needsYouBadge = (page: Page): Locator => page.getByTestId(T.badge);
/** "1 thing needs you" / "3 things need you" (`needsYou.badgeAria_*`). */
export const badgeLabel = (count: number): string => (count === 1 ? '1 thing needs you' : `${count} things need you`);
export const menuDot = (page: Page): Locator => page.getByTestId(T.menuDot);

/** `/jobs/?view=…` (D6). */
export const JOBS_URL = { needsYou: '/jobs/?view=needs-you', all: '/jobs/?view=all' } as const;
export const JOBS_PAGE_SELECTOR = `[data-testid="${T.jobsPage}"]`;
export const jobsTab = (page: Page, name: 'Needs you' | 'Jobs'): Locator => page.getByRole('tab', { name: new RegExp(`^${name}`) });
export const jobRow = (page: Page, runId: string): Locator =>
  page.locator(`[data-testid="${T.jobRow}"][data-run-id="${runId}"]`);
export const needsYouRows = (page: Page): Locator => page.getByTestId(T.needsYouRow);
/** A Jobs page section by its heading (`jobsSections()`: Needs you · Working · Up next · Finished). */
export const jobsSection = (page: Page, heading: 'Needs you' | 'Working' | 'Up next' | 'Finished'): Locator =>
  page.getByRole('region', { name: heading, exact: true });

/** The words a stop reason reads in (D12). */
export const STOPPED_BY_YOU = 'Stopped by you';
export const ALREADY_FINISHED = 'It had already finished';

// ---------------------------------------------------------------------------------------------------------------------
// Turns and jobs.
// ---------------------------------------------------------------------------------------------------------------------

/**
 * Sends a scenario's turn from the page and returns the conversation. A loop scenario's turn goes in deep mode, the
 * only mode whose coordinator hands the whole turn to one sub-agent; the page's own mode choice is replaced on the
 * wire, after the scripted-model pin, which still applies (a page route falls back to the context's).
 */
export async function sendJobTurn(page: Page, l: Lane, scenario: JobScenario): Promise<{ agentKey: string; conversationId: string }> {
  const agentKey = scenario.stack === 'engine' ? engineAgentKey() : l.toolAgentKey;
  if (scenario.stack === 'loop') {
    await page.route(
      (url) => TURN_STREAM.test(url.pathname),
      async (route) => {
        const request = route.request();
        if (request.method() !== 'POST') return route.fallback();
        const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;
        return route.fallback({ postData: JSON.stringify({ ...body, chatMode: 'deep' }) });
      },
    );
  }
  const conversationId = await sendTurn(page, agentKey, scenario.prompt);
  return { agentKey, conversationId };
}

/**
 * The engine lane's stack (`run.sh durable`): the scripted model and SaaS as on the harness lane, but its seed has no
 * tool agent, so `lane()` would refuse it. Engine scenarios use only the engine agent.
 */
export function engineLane(): Lane {
  const vars = { llmUrl: 'E2E_LLM_URL', saasUrl: 'E2E_SAAS_URL', control: 'E2E_LLM_CONTROL', agentKey: 'E2E_AGENT_KEY' } as const;
  const missing = [...Object.values(vars), 'E2E_ENGINE_AGENT_KEY'].filter((name) => !process.env[name]);
  if (missing.length > 0) throw new Error(`not on the engine lane (run.sh durable): ${missing.join(', ')} unset`);
  const env = (name: string) => process.env[name] as string;
  return { llmUrl: env(vars.llmUrl), saasUrl: env(vars.saasUrl), control: env(vars.control), agentKey: env(vars.agentKey), toolAgentKey: '' };
}

/** The engine agent the durable lane seeds (`run.sh durable`); its absence fails the spec, never skips it. */
export function engineAgentKey(): string {
  const key = process.env.E2E_ENGINE_AGENT_KEY;
  if (!key) throw new Error('not on the engine lane (run.sh durable): E2E_ENGINE_AGENT_KEY unset');
  return key;
}

type ChildView = { runId: string; status: string };
export type JobTimeline = {
  status: string;
  approvals: Array<{ interactionId: string; status: string; toolName?: string }>;
  children: ChildView[];
  stoppedBy?: { scope: string; reason: string };
};

export async function jobTimeline(api: APIRequestContext, agentKey: string, conversationId: string, runId: string): Promise<JobTimeline> {
  const res = await api.get(`${conversationPath(agentKey, conversationId)}/runs/${runId}/timeline`);
  expect(res.ok(), `timeline of ${runId}: ${res.status()}`).toBe(true);
  return (await res.json()) as JobTimeline;
}

/** The conversation's running run (the turn), then the job it handed its work to: the turn's first child. */
export async function handedOffJob(
  api: APIRequestContext,
  agentKey: string,
  conversationId: string,
): Promise<{ turnRunId: string; jobRunId: string }> {
  let turnRunId: string | undefined;
  let jobRunId: string | undefined;
  await expect
    .poll(
      async () => {
        if (!turnRunId) {
          const res = await api.get(conversationPath(agentKey, conversationId));
          turnRunId = res.ok() ? ((await res.json()) as { conversation?: { activeRunId?: string } }).conversation?.activeRunId : undefined;
        }
        if (turnRunId) jobRunId = (await jobTimeline(api, agentKey, conversationId, turnRunId)).children[0]?.runId;
        return jobRunId ?? null;
      },
      { message: 'the turn handed its work to a job', timeout: 240_000 },
    )
    .not.toBeNull();
  return { turnRunId: turnRunId as string, jobRunId: jobRunId as string };
}

/**
 * Lets every held model call go until `until` holds. Each poll releases a call the scripted model is holding, so a
 * job that holds again after a resume moves on too; the wait is on the condition, not a count of releases.
 */
export async function releaseUntil(model: ScriptedModel, until: () => Promise<boolean>, message: string, timeout = 240_000): Promise<void> {
  await expect
    .poll(
      async () => {
        if (await until()) return true;
        if (await model.holding()) await model.release();
        return false;
      },
      { message, timeout, intervals: [250, 500, 1_000] },
    )
    .toBe(true);
}

/** The job's first ask still waiting, once it asks. */
export async function pendingAsk(api: APIRequestContext, agentKey: string, conversationId: string, runId: string): Promise<string> {
  let interactionId: string | undefined;
  await expect
    .poll(
      async () => {
        const { approvals } = await jobTimeline(api, agentKey, conversationId, runId);
        interactionId = approvals.find((a) => a.status === 'pending')?.interactionId;
        return interactionId ?? null;
      },
      { message: 'the job asks the person', timeout: 240_000 },
    )
    .not.toBeNull();
  return interactionId as string;
}

/** Allows a job's ask through the run's own decision route, as the chat's card does. */
export async function allowAsk(api: APIRequestContext, agentKey: string, conversationId: string, runId: string, interactionId: string): Promise<void> {
  const res = await api.post(`${conversationPath(agentKey, conversationId)}/runs/${runId}/interactions/${interactionId}/decision`, {
    data: { decision: 'approved', idempotencyKey: `cj5-allow-${interactionId}` },
  });
  expect(res.ok(), `allowing ${interactionId}: ${res.status()} ${await res.text()}`).toBe(true);
}

/** The person's count of asks waiting on them, as the server counts it (D7: the badge must equal it). */
export async function waitingCount(api: APIRequestContext): Promise<number> {
  const res = await api.get('/api/v1/agent-harness/inbox/count');
  expect(res.ok(), `inbox count: ${res.status()}`).toBe(true);
  return ((await res.json()) as { waiting: number }).waiting;
}

type McpGrant = { instanceId: string; name: string; tools: Array<{ name: string; fullName: string }> };

/** The seed grants the tool agent the SaaS's write tool only; a scenario that reads gets the read tool added. */
export async function grantTools(api: APIRequestContext, agentKey: string, wanted: string[]): Promise<void> {
  const read = await api.get(`/api/v1/agents/${agentKey}`);
  expect(read.ok()).toBe(true);
  const servers = ((await read.json()) as { agent?: { mcpServers?: McpGrant[] } }).agent?.mcpServers ?? [];
  expect(servers.length, 'the seeded agent has the scripted SaaS').toBeGreaterThan(0);
  const [server] = servers;
  const missing = wanted.filter((name) => !server!.tools.some((tool) => tool.name === name));
  if (missing.length === 0) return;
  const tools = [...server!.tools, ...missing.map((name) => ({ name, fullName: `mcp_${server!.name}_${name}` }))].map(
    ({ name, fullName }) => ({ name, fullName }),
  );
  const write = await api.put(`/api/v1/agents/${agentKey}`, {
    data: { mcpServers: [{ instanceId: server!.instanceId, name: server!.name, tools }] },
  });
  expect(write.ok(), `grant ${missing.join(', ')}: ${write.status()} ${await write.text()}`).toBe(true);
}

/** `text` as a literal inside a RegExp, so a goal or ask id with `(`, `?` or `\\` still matches only itself. */
export function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// ---------------------------------------------------------------------------------------------------------------------
// A second person: the shared-chat viewer (D14).
// ---------------------------------------------------------------------------------------------------------------------

export type Viewer = { email: string; password: string; userId: string };

/** A member of the org who signs in with a password, made through the admin's API (as `seed_human.py` does). */
export async function createViewer(api: APIRequestContext): Promise<Viewer> {
  // Node sets a starting password only for the demo domain; anyone else is invited and picks their own.
  const email = `cj5-viewer-${Date.now()}@acme-demo.example`;
  const password = `Cj5-${randomBytes(6).toString('hex')}!Aa1`;
  const res = await api.post('/api/v1/users', { data: { fullName: 'Casey Viewer', email, role: 'member', password } });
  expect(res.ok(), `creating the viewer: ${res.status()} ${await res.text()}`).toBe(true);
  const body = (await res.json()) as { user?: { _id?: string; id?: string }; _id?: string; id?: string };
  const userId = body.user?._id ?? body.user?.id ?? body._id ?? body.id;
  expect(userId, 'the member create names the new user').toBeTruthy();
  return { email, password, userId: userId as string };
}

export async function removeViewer(api: APIRequestContext, viewer: Viewer | undefined): Promise<void> {
  if (viewer) await deleteUserByEmail(api, viewer.email);
}

/** Shares the agent conversation read-only with the viewer (owner only). */
export async function shareWith(api: APIRequestContext, agentKey: string, conversationId: string, viewer: Viewer): Promise<void> {
  const res = await api.post(`${conversationPath(agentKey, conversationId)}/share`, { data: { userIds: [viewer.userId], accessLevel: 'read' } });
  expect(res.ok(), `sharing ${conversationId}: ${res.status()} ${await res.text()}`).toBe(true);
}

/** The viewer's own signed-in page, its turns pinned to the scripted model like every page on the lane. */
export async function viewerPage(browser: Browser, viewer: Viewer): Promise<Page> {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  await pinScriptedModel(context);
  const page = await context.newPage();
  await loginViaUI(page, viewer.email, viewer.password);
  return page;
}

// ---------------------------------------------------------------------------------------------------------------------
// Probes: frames and statuses on the page's own clock.
// ---------------------------------------------------------------------------------------------------------------------

export type ProbedFrame = { runId: string; source: 'job' | 'chat'; type: string; name?: string; at: number };
export type ProbedStatus = { runId: string; status: string; at: number };
type Probes = { frames: ProbedFrame[]; statuses: ProbedStatus[] };

/**
 * Before any page script runs: wraps `fetch` so each frame of a run's own stream (`…/runs/:id/stream`, read with
 * fetch because EventSource cannot send the token) is recorded when it arrives, and watches every job card's
 * `data-status`. Both read `performance.now()`, so "the card changed 120 ms after the frame" is one clock.
 */
export async function installJobProbes(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __cjProbes?: Probes; fetch: typeof fetch };
    if (w.__cjProbes) return;
    const probes: Probes = { frames: [], statuses: [] };
    w.__cjProbes = probes;
    const STREAM = /\/runs\/([^/?]+)\/stream(?:\?|$)/;
    const original = w.fetch.bind(window);
    w.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await original(input, init);
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      // Every event stream: a job's own, and the chat's, whose frames also say when a job it handed work to ended.
      const sse = (response.headers.get('content-type') ?? '').includes('text/event-stream');
      if (!sse || !response.body) return response;
      const match = STREAM.exec(url);
      const streamRunId = match ? decodeURIComponent(match[1]) : '';
      const source = match ? 'job' : 'chat';
      const decoder = new TextDecoder();
      let buffer = '';
      const read = (text: string, at: number, last: boolean) => {
        buffer += text;
        const blocks = buffer.split(/\r?\n\r?\n/);
        // The stream's last frame may close it with no blank line after it: it is still a frame.
        buffer = last ? '' : (blocks.pop() ?? '');
        for (const block of blocks) {
          const lines = block.split(/\r?\n/);
          const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
          // The frame's type is in its data, or only on its `event:` line (as `frameOf` reads it).
          const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim();
          if (!data) continue;
          try {
            const frame = JSON.parse(data) as { type?: string; name?: string; runId?: string; stepName?: string };
            const runId = typeof frame.runId === 'string' && frame.runId ? frame.runId : streamRunId;
            const name = frame.name ?? frame.stepName;
            probes.frames.push({ runId, source, type: String(frame.type ?? event ?? ''), ...(name ? { name } : {}), at });
          } catch {
            // A keep-alive comment or a partial line: not a frame.
          }
        }
      };
      // Each chunk is read on its way to the page, before the page sees it: the page aborts the request once it
      // has the end frame, and a tee'd copy would lose what it had not read yet.
      const theirs = response.body.pipeThrough(
        new TransformStream<Uint8Array, Uint8Array>({
          transform(chunk, controller) {
            read(decoder.decode(chunk, { stream: true }), performance.now(), false);
            controller.enqueue(chunk);
          },
          flush() {
            read(decoder.decode(), performance.now(), true);
          },
        }),
      );
      return new Response(theirs, { status: response.status, statusText: response.statusText, headers: response.headers });
    };
    const seen = new Map<string, string>();
    const record = () => {
      for (const card of Array.from(document.querySelectorAll<HTMLElement>('[data-testid="job-card"]'))) {
        const runId = card.dataset.runId ?? '';
        const status = card.dataset.status ?? '';
        if (seen.get(runId) === status) continue;
        seen.set(runId, status);
        probes.statuses.push({ runId, status, at: performance.now() });
      }
    };
    new MutationObserver(record).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-status'] });
  });
}

export async function probes(page: Page): Promise<Probes> {
  return page.evaluate(() => (window as unknown as { __cjProbes: Probes }).__cjProbes);
}

/** The statuses one job's card went through, in order, each once. */
export async function statusTrail(page: Page, runId: string): Promise<string[]> {
  return (await probes(page)).statuses.filter((s) => s.runId === runId).map((s) => s.status);
}

/**
 * The frames that end a job: its own stream's end, or the chat's step for it finishing. A card may stop following
 * the job's stream once the chat's copy says it ended, so whichever came first is the frame the card answers to.
 */
export const JOB_END_FRAMES = ['RUN_FINISHED', 'RUN_ERROR', 'STEP_FINISHED'];

/**
 * §6 "card status updates within 500 ms of the frame": the time from the first of `frameTypes` naming the job (on its
 * own stream or the chat's) to the card first showing `status`. Fails when either never happened, so a card fed by
 * something else (a poll) shows.
 */
export async function frameToStatusMs(page: Page, runId: string, frameTypes: string[], status: JobStatus): Promise<number> {
  // The card may have its status from the answer's own copy of the job first; the stream's frame still comes.
  await expect
    .poll(async () => (await probes(page)).frames.some((f) => f.runId === runId && frameTypes.includes(f.type)), {
      message: `the job's stream carries ${frameTypes.join(' or ')}`,
      timeout: 30_000,
    })
    .toBe(true)
    .catch(() => undefined);
  const { frames, statuses } = await probes(page);
  const frame = frames.filter((f) => f.runId === runId && frameTypes.includes(f.type)).sort((a, b) => a.at - b.at)[0];
  const shown = statuses.find((s) => s.runId === runId && s.status === status);
  const seen = frames.filter((f) => f.runId === runId).map((f) => `${f.source}:${f.type}`);
  expect(frame, `the job's stream carried ${frameTypes.join(' or ')} (it carried: ${seen.join(', ') || 'nothing'})`).toBeTruthy();
  expect(shown, `the card showed ${status}`).toBeTruthy();
  return shown!.at - frame!.at;
}

/** §6 "the panel's first paint under 300 ms after a click": a click on the card's Details to the job header on screen. */
export async function clickToPanelMs(page: Page, card: Locator): Promise<number> {
  await showCard(page, card);
  const target = detailsButton(card);
  await centre(target);
  await page.evaluate(() => {
    const w = window as unknown as { __cjClickAt?: number };
    delete w.__cjClickAt;
    document.addEventListener('click', () => { w.__cjClickAt = performance.now(); }, { capture: true, once: true });
  });
  await target.click();
  const handle = await page.waitForFunction(
    () => {
      const header = document.querySelector('[data-testid="activity-panel"] [data-testid="job-panel-header"]');
      const at = (window as unknown as { __cjClickAt?: number }).__cjClickAt;
      return header && header.getClientRects().length > 0 && at !== undefined ? performance.now() - at : false;
    },
    null,
    { polling: 'raf', timeout: 30_000 },
  );
  return (await handle.jsonValue()) as number;
}

export type ApiCall = { method: string; path: string; at: number };

/** Every request the page sends to the API from now on, in order. */
export function recordApiCalls(page: Page): ApiCall[] {
  const calls: ApiCall[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) calls.push({ method: request.method(), path: url.pathname, at: Date.now() });
  });
  return calls;
}

/** The calls that name one run, as `{method} /runs/:id/<rest>` with the session part dropped. */
export function runCalls(calls: ApiCall[], runId: string): string[] {
  const marker = `/runs/${runId}`;
  return calls
    .filter((c) => c.path.includes(marker))
    .map((c) => `${c.method} ${c.path.slice(c.path.indexOf(marker)).replace(runId, ':id')}`);
}

/** The §6 budgets. */
export const BUDGETS = { frameToCardMs: 500, clickToPanelMs: 300 } as const;

// ---------------------------------------------------------------------------------------------------------------------
// Accessibility and pictures.
// ---------------------------------------------------------------------------------------------------------------------

const AXE_SOURCE = fs.readFileSync(createRequire(__filename).resolve('axe-core/axe.min.js'), 'utf8');
const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

export type AxeViolation = { id: string; impact: string | null; help: string; targets: string[]; html: string[]; why: string[] };

/**
 * axe (a pinned devDependency) over `include` against WCAG 2.1 A and AA. Evaluated rather than injected as a script
 * tag: the app's CSP refuses inline scripts, and an evaluation is not one.
 */
export async function axeViolations(page: Page, include: string[] = ['body']): Promise<AxeViolation[]> {
  if (!(await page.evaluate(() => 'axe' in window))) await page.evaluate(AXE_SOURCE);
  return page.evaluate(
    async ({ tags, selectors }) => {
      type AxeResult = {
        violations: Array<{ id: string; impact: string | null; help: string; nodes: Array<{ target: string[]; html: string; failureSummary?: string }> }>;
      };
      const axe = (window as unknown as { axe: { run: (ctx: unknown, opts: unknown) => Promise<AxeResult> } }).axe;
      const result = await axe.run(
        { include: selectors.map((s) => [s]) },
        { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] },
      );
      // A selector alone rarely names the element on a page-wide pass: its markup and axe's reason come with it.
      return result.violations.map((v) => {
        const nodes = v.nodes.slice(0, 5);
        return {
          id: v.id,
          impact: v.impact,
          help: v.help,
          targets: nodes.map((n) => n.target.join(' ')),
          html: nodes.map((n) => n.html.slice(0, 200)),
          why: nodes.map((n) => (n.failureSummary ?? '').slice(0, 300)),
        };
      });
    },
    { tags: WCAG, selectors: include },
  );
}

export async function expectNoAxeViolations(page: Page, label: string, include?: string[]): Promise<void> {
  const violations = await axeViolations(page, include);
  expect(violations, `${label}: ${JSON.stringify(violations, null, 1)}`).toEqual([]);
}

/** Controls inside `root` under 44 by 44 CSS pixels (§4.1, §9), with their names. Hidden ones are not counted. */
export async function smallTouchTargets(root: Locator): Promise<Array<{ name: string; width: number; height: number }>> {
  return root.evaluate((scope) => {
    const small: Array<{ name: string; width: number; height: number }> = [];
    for (const el of Array.from(scope.querySelectorAll<HTMLElement>('button, [role="button"], a[href], [role="tab"]'))) {
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none' || el.closest('[aria-hidden="true"]')) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      if (rect.width + 0.5 < 44 || rect.height + 0.5 < 44) {
        small.push({ name: el.getAttribute('aria-label') || el.textContent?.trim().slice(0, 40) || el.tagName, width: Math.round(rect.width), height: Math.round(rect.height) });
      }
    }
    return small;
  });
}

export const DESKTOP = { width: 1440, height: 960 } as const;
export const PHONE = { width: 390, height: 844 } as const;
export type Look = { name: string; viewport: { width: number; height: number }; dark: boolean };
/** §9's four looks: desktop and phone, light and dark. */
export const LOOKS: readonly Look[] = [
  { name: 'desktop-light', viewport: DESKTOP, dark: false },
  { name: 'desktop-dark', viewport: DESKTOP, dark: true },
  { name: 'phone-light', viewport: PHONE, dark: false },
  { name: 'phone-dark', viewport: PHONE, dark: true },
];

/** Puts the page in a look; the app reads its theme from local storage on load, so the page is reloaded. */
export async function useLook(page: Page, look: Look): Promise<void> {
  await page.setViewportSize(look.viewport);
  await page.emulateMedia({ colorScheme: look.dark ? 'dark' : 'light' });
  await page.evaluate((dark) => localStorage.setItem('pipeshub-theme-preference', dark ? 'dark' : 'light'), look.dark);
  await page.reload();
}

/**
 * Saves a picture for the evidence when the lane gives a folder (`E2E_UI_CASE_SHOTS=/work/pw/<dir>` on run.sh);
 * otherwise does nothing. Waits for the icon font first, so a
 * picture never shows the icons' names.
 */
export async function shoot(page: Page, name: string): Promise<void> {
  const dir = process.env.UI_CASE_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(dir, `cj5-${name}.png`) });
}

