/**
 * The harness lane's stack (deployment/e2e/run.sh harness): the scripted model and SaaS behind their control
 * routes, the seeded agents, and the few reads a spec makes of the stack itself.
 *
 * Nothing here names the route a chat turn is admitted on: a turn is typed into the page, the conversation id is
 * read from the page's URL and the run id from the conversation's `activeRunId` (the reattach contract every
 * client keeps), so the specs hold whether the page admits a run on `…/runs` or on the chat stream route (LAT1).
 */
import { expect, request as playwrightRequest, type APIRequestContext, type Page } from '@playwright/test';

export interface Lane {
  agentKey: string;
  toolAgentKey: string;
  llmUrl: string;
  saasUrl: string;
  control: string;
}

const LANE_VARS = {
  agentKey: 'E2E_AGENT_KEY',
  toolAgentKey: 'E2E_TOOL_AGENT_KEY',
  llmUrl: 'E2E_LLM_URL',
  saasUrl: 'E2E_SAAS_URL',
  control: 'E2E_LLM_CONTROL',
} as const satisfies Record<keyof Lane, string>;

/** The lane's variables; missing ones fail the spec (a skip on a real-service lane would read green). */
export function lane(): Lane {
  const missing = Object.values(LANE_VARS).filter((name) => !process.env[name]);
  if (missing.length > 0) throw new Error(`not on the harness lane (run.sh harness): ${missing.join(', ')} unset`);
  return Object.fromEntries(
    Object.entries(LANE_VARS).map(([key, name]) => [key, process.env[name] as string]),
  ) as unknown as Lane;
}

/** The scripted model's and SaaS's control routes alone, which every harness project's lane has (no seeded agents). */
export function controls(): Pick<Lane, 'llmUrl' | 'saasUrl' | 'control'> {
  const names = { llmUrl: LANE_VARS.llmUrl, saasUrl: LANE_VARS.saasUrl, control: LANE_VARS.control };
  const missing = Object.values(names).filter((name) => !process.env[name]);
  if (missing.length > 0) throw new Error(`not on a harness lane: ${missing.join(', ')} unset`);
  return { llmUrl: process.env[names.llmUrl]!, saasUrl: process.env[names.saasUrl]!, control: process.env[names.control]! };
}

/** A turn's directives for the scripted model, one per line (harness_core.testing.engines.script). */
export const script = (...lines: string[]): string => lines.join('\n');

/**
 * The names an engine gives its shell tool, which the scripted model's `shell` directive calls (SHELL_TOOLS in
 * backend/python/harness_core/testing/engines/script.py; test_r25_engine_lane.py keeps the two equal). The durable
 * lane runs any release engine (E2E_ENGINE): opencode's is `bash`, Claude Code's `Bash`.
 */
export const ENGINE_SHELL_TOOLS: readonly string[] = ['Bash', 'bash', 'exec_command', 'run_shell_command', 'shell'];

type ModelRequest = { path: string; status: number; kind?: string; replyToolCalls?: string[]; at: number };
type Effect = { tool: string; argsDigest: string; ticket: string };

async function controlContext(baseURL: string, token: string): Promise<APIRequestContext> {
  return playwrightRequest.newContext({ baseURL, extraHTTPHeaders: { 'X-E2E-Control': token } });
}

export class ScriptedModel {
  private constructor(private readonly ctx: APIRequestContext) {}

  static async open(l: Pick<Lane, 'llmUrl' | 'control'>): Promise<ScriptedModel> {
    return new ScriptedModel(await controlContext(l.llmUrl, l.control));
  }

  async reset(): Promise<void> {
    expect((await this.ctx.post('/__control/reset')).ok()).toBe(true);
  }

  async holding(): Promise<boolean> {
    return ((await (await this.ctx.get('/__control/holding')).json()) as { holding: boolean }).holding;
  }

  async release(): Promise<void> {
    expect((await this.ctx.post('/__control/release')).ok()).toBe(true);
  }

  /** The chat-loop calls since the last reset (title generation and health checks are not `loop`). */
  async loopCalls(): Promise<ModelRequest[]> {
    const { requests } = (await (await this.ctx.get('/__control/requests')).json()) as { requests: ModelRequest[] };
    return requests.filter((r) => r.kind === 'loop');
  }

  /** Watches every model request for these values from now on; a later call replaces them and clears the sightings. */
  async watchFor(values: string[]): Promise<void> {
    expect((await this.ctx.post('/__control/canaries', { data: { values } })).ok()).toBe(true);
  }

  /** The requests a watched value reached, by path (the model keeps no content). */
  async sightings(): Promise<Array<{ path: string }>> {
    return ((await (await this.ctx.get('/__control/canaries')).json()) as { hits: Array<{ path: string }> }).hits;
  }

  async close(): Promise<void> {
    await this.ctx.dispose();
  }
}

export class ScriptedSaas {
  private constructor(private readonly ctx: APIRequestContext) {}

  static async open(l: Pick<Lane, 'saasUrl' | 'control'>): Promise<ScriptedSaas> {
    return new ScriptedSaas(await controlContext(l.saasUrl, l.control));
  }

  async reset(): Promise<void> {
    expect((await this.ctx.post('/__control/reset')).ok()).toBe(true);
  }

  async effects(): Promise<Effect[]> {
    return ((await (await this.ctx.get('/__control/effects')).json()) as { effects: Effect[] }).effects;
  }

  /** A document the SaaS's own search finds (`search_documents`, ANSWER-CONTRACT-SOURCES). */
  async addDocument(title: string, body: string): Promise<void> {
    expect((await this.ctx.post('/__control/documents', { data: { title, body } })).ok()).toBe(true);
  }

  async close(): Promise<void> {
    await this.ctx.dispose();
  }
}

export const chatInput = (page: Page) => page.locator('textarea').last();

/** Every chat turn of a page goes to the scripted model E2E_LLM_MODEL_KEY names: one implementation for the lane. */
export { pinScriptedModel } from './scripted-model';

/** This worker's turns since the last cleanup (`cancelLeftRuns`). */
const sentTurns: Array<{ agentKey: string; conversationId: string }> = [];

/** Types a turn into the agent's chat and answers the conversation the page moved to. */
export async function sendTurn(page: Page, agentKey: string, text: string): Promise<string> {
  await page.goto(`/chat/?agentId=${agentKey}`);
  const input = chatInput(page);
  await input.waitFor({ timeout: 30_000 });
  await input.fill(text);
  await input.press('Enter');
  await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
  const id = new URL(page.url()).searchParams.get('conversationId');
  expect(id, 'the page names its conversation').toBeTruthy();
  trackTurn(agentKey, id as string);
  return id as string;
}

/** Records a turn a page sent, so `cancelLeftRuns` cancels whatever it leaves running. Every sender calls it. */
export function trackTurn(agentKey: string, conversationId: string): void {
  sentTurns.push({ agentKey, conversationId });
}

export const hasLeftTurns = (): boolean => sentTurns.length > 0;

/**
 * WP-71: a spec that fails mid-turn leaves its run waiting on a person, and the Inbox (every later spec's, every
 * person's on a shared stack) then lists it. After each test, whatever this worker's turns left running
 * is cancelled through the run's own cancel route.
 */
export async function cancelLeftRuns(api: APIRequestContext): Promise<void> {
  for (const { agentKey, conversationId } of sentTurns.splice(0)) {
    const res = await api.get(conversationPath(agentKey, conversationId));
    if (!res.ok()) continue;
    const runId = ((await res.json()) as Conversation).conversation?.activeRunId;
    if (!runId) continue;
    const cancelled = await api.post(`${conversationPath(agentKey, conversationId)}/runs/${runId}/cancel`, {
      data: { idempotencyKey: `e2e-cleanup-${runId}` },
    });
    // 409: it ended between the read and the cancel.
    expect([200, 202, 409], `cancelling the left run ${runId}: ${cancelled.status()}`).toContain(cancelled.status());
  }
}

export const conversationPath = (agentKey: string, conversationId: string): string =>
  `/api/v1/agents/${agentKey}/conversations/${conversationId}`;

type Conversation = { conversation?: { activeRunId?: string | null } };

async function readConversation(api: APIRequestContext, agentKey: string, conversationId: string): Promise<Conversation> {
  const res = await api.get(conversationPath(agentKey, conversationId));
  expect(res.ok(), `conversation ${conversationId}: ${res.status()}`).toBe(true);
  return (await res.json()) as Conversation;
}

/** The conversation's running run, as a reattaching client reads it. */
export async function activeRunId(api: APIRequestContext, agentKey: string, conversationId: string): Promise<string> {
  let runId: string | null | undefined;
  await expect
    .poll(async () => {
      runId = (await readConversation(api, agentKey, conversationId)).conversation?.activeRunId;
      return runId ?? null;
    }, { message: 'the conversation has a running run', timeout: 60_000 })
    .not.toBeNull();
  return runId as string;
}

export async function noActiveRun(api: APIRequestContext, agentKey: string, conversationId: string): Promise<void> {
  await expect
    .poll(async () => (await readConversation(api, agentKey, conversationId)).conversation?.activeRunId ?? null, {
      message: 'the conversation has no running run',
      timeout: 60_000,
    })
    .toBeNull();
}

export type Timeline = {
  status: string;
  approvals: Array<{ status: string; tool?: string; decision?: string }>;
};

export async function runTimeline(
  api: APIRequestContext,
  agentKey: string,
  conversationId: string,
  runId: string,
): Promise<Timeline> {
  const res = await api.get(`${conversationPath(agentKey, conversationId)}/runs/${runId}/timeline`);
  expect(res.ok(), `timeline: ${res.status()}`).toBe(true);
  return (await res.json()) as Timeline;
}

/** The scripted model is holding the run's call: the run is mid-turn, waiting on its model. */
export async function untilHolding(model: ScriptedModel): Promise<void> {
  await expect.poll(() => model.holding(), { message: 'the model call is held', timeout: 90_000 }).toBe(true);
}

/** Every POST body the page sends to the API, to show what the browser does not name (e.g. a model account). */
export function recordPostBodies(page: Page): string[] {
  const bodies: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/api/')) {
      bodies.push(request.postData() ?? '');
    }
  });
  return bodies;
}

/** A finished turn keeps its trace, its approval cards among it, in a section that starts collapsed. */
export async function openFinishedActivity(page: Page): Promise<void> {
  // The summary line names what the run did (CTL-3b), so it is found by its role in the answer, not its words.
  const summary = page.getByTestId('activity-summary').last();
  await expect(summary).toBeVisible({ timeout: 30_000 });
  if ((await summary.getAttribute('aria-expanded')) === 'false') await summary.click();
}

/** A tool id as a card words it: `create_ticket` is "Create ticket" (UXINT-3). */
const toolWords = (tool: string): string => tool.replace(/_/g, '[ _]');
/**
 * A tool's approval card, whichever rule asked (the org's "ask", the flow guard, an engine's writes): the title says
 * what the call does ("Create ticket in E2Esaas?"), so the tool id is matched loosely.
 */
export const allowOnceButton = (page: Pick<Page, 'getByRole'>, tool: string) =>
  page.getByRole('button', { name: new RegExp(`^Allow once: .*${toolWords(tool)}`, 'i') });
/**
 * The header's count of what waits, with since when the longest-waiting card was asked (WP-97): on this lane a card
 * is asked moments before the check, so its time is today's, as a clock time in the browser's locale.
 */
export const waitingCountText = (count: number): RegExp => new RegExp(`^${count} waiting for you since \\d{1,2}:\\d{2}(\\s[AP]M)?$`);
export const denyButton = (page: Page, tool: string) => page.getByRole('button', { name: new RegExp(`^Deny .*${toolWords(tool)}`, 'i') });

/**
 * Deny opens the card's reason field (AP-05, WP-10); Deny again sends the denial, with the reason when one is given.
 * Returns once the second Deny is clicked.
 */
export async function denyCall(page: Page, tool: string, reason?: string, timeout = 90_000): Promise<void> {
  await denyButton(page, tool).click({ timeout });
  if (reason !== undefined) await page.getByTestId('deny-reason-input').fill(reason);
  await page.getByTestId('deny-confirm').click();
}
/** A card on the page, or inside a part of it (a child's own timeline). */
export const approvalCard = (page: Pick<Page, 'getByRole'>, tool: string) =>
  page.getByRole('region', { name: new RegExp(tool.replace(/_/g, '[ _]'), 'i') }).last();
