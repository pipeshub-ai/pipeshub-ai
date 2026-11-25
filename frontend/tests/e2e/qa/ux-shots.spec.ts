/**
 * QA-CLASSIC (was QA-AUTO's new-interface captures): the classic UI's pages and key states, captured at desktop and
 * phone width in light and dark, and compared with their baselines (pixel diff, tolerance in ux-captures.json), so
 * a visual regression fails. The states include the job card and job panel, the Jobs page and Needs you.
 *
 * Runs on the harness lane's stack with the scripted model (`ux-shots` project), first on a fresh stack
 * (scripts/qa_full.py), so what the captures show (the sidebar's chats, what waits in Needs you) is the same every
 * run: every text the spec types is fixed, and the states are made in one order. Baselines live outside git, one
 * folder per Playwright version (`scripts/qa_full.sh --lanes ux --update-ux-baselines` makes them); each capture is
 * also written to the report's gallery.
 *
 * What changes with the clock or the run (times, dates, a job's elapsed time) is masked.
 *
 * QA-FINAL: every capture is also an axe pass (WCAG 2.1 A and AA) over what it shows, in each width and scheme, so
 * the pages the other a11y specs never open (chat, cards, agent builder, settings) are held to it too. A page that
 * still breaks a rule lists it under `axeKnown` with the row that tracks it; any other rule fails the capture, and a
 * listed rule no look shows any more fails it too, so the list only shrinks.
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, conversationPath, lane, script, ScriptedModel, ScriptedSaas, type Lane } from '../harness/support/lane';
import { TURN_STREAM } from '../harness/support/scripted-model';
import { adminApi, setLabsFlag } from '../helpers/labs-flag.helper';
import { answerActions, composer, discover, jobCard, jobPanel, JOBS, needsYouRows, openAgentChat, openSteps, showRecents } from './classic';
import { capture, expectManifestLists, followEmulatedScheme, goto, heading, settle } from './ux-capture';
import en from '../../../lib/i18n/locales/en-US.json';

test.describe.configure({ mode: 'serial' });

const SCHEDULES = '/api/v1/agent-harness/schedules';
const SCHEDULE_NAME = 'Weekly P1 digest';
const TOOL = 'create_ticket';
const JOB_GOAL = 'Summarise the release notes for the team.';
const MCP_INSTANCES = '/api/v1/mcp-servers/instances';
const SIGN_IN_SERVER = 'Issue tracker';
/**
 * What "Allow for this chat" left in the answer's chat, served by the spec (as bgui8-whole-flow.spec.ts does): the
 * seeded org's rule asks for the scripted tools, and an org rule is never granted for a chat, so no turn can make one.
 */
const GRANTS = {
  grants: [
    { grantId: `cg_${'a'.repeat(40)}`, toolId: 'slack@i/send_message', tool: { app: 'slack', name: 'send_message' }, destination: '#sales', sensitivity: 'private', grantedBy: null, grantedByName: null, grantedAt: '2026-11-08T10:00:00Z' },
    { grantId: `cg_${'b'.repeat(40)}`, toolId: 'web@i/fetch_url', tool: { app: 'web', name: 'fetch_url' }, destination: 'onyx.app', sensitivity: 'public', grantedBy: null, grantedByName: null, grantedAt: '2026-11-09T15:30:00Z' },
  ],
};

let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;
let schedulesBefore = false;
const urls: Record<string, string> = {};
let scheduleId = '';
let signInServerId = '';
/** The chats the captures keep waiting across tests; cancelled once, after the last capture. */
const waiting: Array<{ agentKey: string; conversationId: string }> = [];

/**
 * A turn in the agent's new chat, without the per-test cleanup: the fixture cancels a tracked turn after each test,
 * and the Needs you captures open these chats in later tests, still waiting.
 */
async function startChat(page: Page, text: string, agentKey: string): Promise<void> {
  await openAgentChat(page, agentKey);
  await composer(page).fill(text);
  await composer(page).press('Enter');
  await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
  waiting.push({ agentKey, conversationId: new URL(page.url()).searchParams.get('conversationId') as string });
}

/** The seeded agent in the builder, loaded: the name field is drawn before the agent arrives, empty. */
async function openBuilder(page: Page): Promise<void> {
  await goto(page, `/agents/edit/?agentKey=${l.agentKey}`, (q) => q.getByPlaceholder('e.g. Support bot'));
  await expect(page.getByPlaceholder('e.g. Support bot')).not.toHaveValue('', { timeout: 60_000 });
  await expect(page.getByText(/^Loading agent/)).toHaveCount(0, { timeout: 60_000 });
  await settle(page);
}

test.beforeAll(async () => {
  l = lane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
  const api = await adminApi();
  schedulesBefore = await setLabsFlag(api, 'ENABLE_SCHEDULED_RUNS', true);
  await api.dispose();
});

test.afterAll(async () => {
  const api = await adminApi();
  for (const { agentKey, conversationId } of waiting) {
    const read = (await (await api.get(conversationPath(agentKey, conversationId))).json()) as { conversation?: { activeRunId?: string | null } };
    const runId = read.conversation?.activeRunId;
    if (runId) await api.post(`${conversationPath(agentKey, conversationId)}/runs/${runId}/cancel`, { data: { idempotencyKey: `qa-ux-${runId}` } });
  }
  if (scheduleId) await api.delete(`${SCHEDULES}/${scheduleId}`);
  if (signInServerId) await api.delete(`${MCP_INSTANCES}/${signInServerId}`);
  await setLabsFlag(api, 'ENABLE_SCHEDULED_RUNS', schedulesBefore);
  await api.dispose();
  await model.close();
  await saas.close();
});

followEmulatedScheme();

test('UX setup: the answer, the job, the waiting approval and question, and the schedule the captures open', async ({ page, apiContext }) => {
  test.setTimeout(300_000);
  await model.reset();
  await saas.reset();
  await startChat(page, script('Summarise the release notes.', `tool search_tools ${JSON.stringify({ query: 'release notes' })}`, 'say The release adds job cards, the Jobs page and Needs you.'), l.agentKey);
  await expect(answerActions(page).last()).toBeVisible({ timeout: 90_000 });
  urls.answer = page.url();

  // R31-AXET-FU-3: a job's reach (its own sandbox, and the code tools that come with it) is pinned when it is
  // admitted, from the stack's last capability probe. On a stack just started that probe can still read "no
  // sandbox", and the job would then differ from the baseline's for good. Admit it once the probe proves one: the
  // chat's /sandbox answer reads the same snapshot.
  const answerId = new URL(urls.answer).searchParams.get('conversationId') as string;
  // ADR-CAP-01 D4: Node's readiness says when every capability probe has answered.
  await expect
    .poll(async () => {
      const res = await apiContext.get('/api/v1/health');
      return res.ok() ? (((await res.json()) as { agentHarness?: { capabilities?: string } }).agentHarness?.capabilities ?? 'absent') : null;
    }, { message: "Node's capabilities settle before the job is admitted", timeout: 120_000 })
    .toMatch(/^(settled|absent)$/);
  await expect
    .poll(async () => {
      const res = await apiContext.get(`${conversationPath(l.agentKey, answerId)}/sandbox`);
      return res.ok() ? (((await res.json()) as { image?: { isolation?: string | null } }).image?.isolation ?? null) : null;
    }, { message: 'the stack proves a code sandbox before the job is admitted', timeout: 120_000 })
    .not.toMatch(/^(none)?$/);

  // A background job: the tool agent's turn in deep mode hands its work to one sub-agent, which finishes.
  await page.route((url) => TURN_STREAM.test(url.pathname), async (route) => {
    const request = route.request();
    if (request.method() !== 'POST') return route.fallback();
    return route.fallback({ postData: JSON.stringify({ ...((request.postDataJSON() ?? {}) as Record<string, unknown>), chatMode: 'deep' }) });
  });
  await startChat(page, script(JOB_GOAL, `tool search_tools ${JSON.stringify({ query: 'release notes' })}`, 'say The release notes are summarised.'), l.toolAgentKey);
  await page.unrouteAll({ behavior: 'wait' });
  await expect(jobCard(page)).toBeVisible({ timeout: 120_000 });
  await expect(jobCard(page)).toHaveAttribute('data-status', 'done', { timeout: 120_000 });
  await expect(answerActions(page).last()).toBeVisible({ timeout: 90_000 });
  urls.job = page.url();

  await startChat(page, script('File a ticket titled Printer on floor 3.', discover(TOOL),
    `tool ${TOOL} ${JSON.stringify({ title: 'Printer on floor 3', body: 'It jams.' })}`, 'say Filed.'), l.toolAgentKey);
  await expect(allowOnceButton(page, TOOL)).toBeVisible({ timeout: 90_000 });
  urls.approval = page.url();

  await startChat(page, script('Write the regional report.', `tool ask_user_question ${JSON.stringify({
    user_intent: 'The report differs by region.',
    questions: [{ question: 'Which region should the report cover?', options: [{ label: 'North America' }, { label: 'Europe' }], multiSelect: false }],
  })}`), l.agentKey);
  await expect(page.getByRole('radio', { name: 'North America' })).toBeVisible({ timeout: 90_000 });
  urls.question = page.url();

  const made = await apiContext.post(SCHEDULES, {
    data: {
      name: SCHEDULE_NAME,
      agentKey: l.agentKey,
      input: { query: script('List the open P1 tickets.', 'say There are no open P1s.') },
      trigger: { kind: 'cron', cron: '0 9 1 1 *', timeZone: 'Europe/Berlin', catchUp: 'skip' },
    },
  });
  expect(made.ok(), `creating the schedule: ${made.status()} ${await made.text()}`).toBe(true);
  scheduleId = ((await made.json()) as { schedule: { id: string } }).schedule.id;

  // A server that wants a person's own key, so the MCP servers page offers Connect and its sign-in dialog.
  const server = await apiContext.post(MCP_INSTANCES, { data: { name: SIGN_IN_SERVER, transport: 'streamable_http', authMode: 'api_token', url: 'http://pxh-fake-saas:8090/mcp' } });
  expect(server.ok(), `creating the MCP server: ${server.status()} ${await server.text()}`).toBe(true);
  const made2 = (await server.json()) as { instance?: { _id?: string }; _id?: string };
  signInServerId = made2.instance?._id ?? made2._id ?? '';
});

test('UX chat: welcome, answer, steps, approval, question', async ({ page }) => {
  await capture(page, 'chat-welcome', (p) => goto(p, '/chat/', (q) => q.getByText('What do you want to explore today?')));
  await capture(page, 'chat-answer', (p) => goto(p, urls.answer, answerActions));
  await capture(page, 'chat-steps-open', async (p) => {
    await goto(p, urls.answer, answerActions);
    await openSteps(p);
    await settle(p);
  });
  await capture(page, 'chat-approval', (p) => goto(p, urls.approval, (q) => allowOnceButton(q, TOOL)));
  await capture(page, 'chat-question', (p) => goto(p, urls.question, (q) => q.getByRole('radio', { name: 'North America' })));
});

test('UX jobs: the job card, its panel, the Jobs page and Needs you', async ({ page }) => {
  // The card alone: where the chat is scrolled to is not the card's design, and a page capture moved by a line
  // between runs as the chat settled.
  await capture(page, 'job-card', async (p) => {
    await goto(p, urls.job, answerActions);
    await openSteps(p);
    await expect(jobCard(p)).toHaveAttribute('data-status', 'done');
    await settle(p);
  }, (p) => jobCard(p));
  await capture(page, 'job-panel', async (p) => {
    await goto(p, urls.job, answerActions);
    await openSteps(p);
    await jobCard(p).getByTestId('job-card-details').click();
    await expect(jobPanel(p)).toBeVisible({ timeout: 30_000 });
    await expect(jobPanel(p).getByTestId('job-panel-header')).toHaveAttribute('data-status', 'done');
    // The brief is read when the panel opens, and the panel follows its newest step: wait for the brief, then
    // show the panel from its top, so the picture does not depend on how far it had scrolled.
    // BGUI-3: an ended job opens on its Brief tab; what it was given is folded under the brief, opened for the picture.
    await expect(jobPanel(p).getByTestId('job-brief-goal')).toBeVisible({ timeout: 30_000 });
    await jobPanel(p).getByRole('button', { name: 'What it was given' }).click();
    await expect(jobPanel(p).getByText('Kept back')).toBeVisible({ timeout: 30_000 });
    // A job admitted without a sandbox fails here, by name, rather than as a pixel diff (R31-AXET-FU-3).
    await expect(jobPanel(p).getByText(en.ui.brief.reach.sandbox)).toBeVisible();
    await jobPanel(p).evaluate((root) => {
      for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) if (el.scrollTop > 0) el.scrollTop = 0;
    });
    await settle(p);
  }, jobPanel);
  await capture(page, 'jobs-list', (p) => goto(p, JOBS.all, (q) => q.getByRole('region', { name: 'Finished', exact: true }).getByTestId('job-row')));
  await capture(page, 'needs-you', async (p) => {
    await goto(p, JOBS.needsYou, needsYouRows);
    await expect(needsYouRows(p)).toHaveCount(2);
  });
});

test('UX the agent builder, collections, connectors', async ({ page }) => {
  await capture(page, 'agent-builder', openBuilder);
  await capture(page, 'collections', (p) => goto(p, '/knowledge-base/', (q) => q.getByText(/^Showing \d/)));
  await capture(page, 'connectors', (p) => goto(p, '/connectors/', heading));
});

test("UX the agent's versions", async ({ page }) => {
  // The versions panel opens beside the builder: the page, so its header and how it sits over the canvas show too.
  await capture(page, 'agent-versions', async (p) => {
    await openBuilder(p);
    await p.getByRole('button', { name: /Versions$/ }).click();
    await expect(p.getByTestId('agent-versions-panel').getByTestId('agent-version-row').first()).toBeVisible({ timeout: 30_000 });
    await settle(p);
  });
});

test('UX permissions in this chat', async ({ page }) => {
  const conversationId = new URL(urls.answer).searchParams.get('conversationId') as string;
  await page.route((url) => url.pathname.endsWith(`/conversations/${conversationId}/grants`), (route) =>
    route.request().method() === 'GET' ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(GRANTS) }) : route.fallback());
  await capture(page, 'chat-grants', async (p) => {
    await goto(p, urls.answer, answerActions);
    await p.getByTestId('chat-grants-button').click();
    await expect(p.getByTestId('chat-grants-panel').getByText('#sales')).toBeVisible({ timeout: 30_000 });
    await settle(p);
  }, (p) => p.getByTestId('chat-grants-panel'));
  await page.unrouteAll({ behavior: 'wait' });
});

test('UX admin: AI access, Labs', async ({ page }) => {
  await capture(page, 'settings-ai-access', (p) => goto(p, '/workspace/ai-access/', heading));
  await capture(page, 'settings-labs', (p) => goto(p, '/workspace/labs/', (q) => q.getByRole('switch')));
});

// One row per server, no buttons on a row: a server's tools, approvals and sign-in are on its own page (`?serverId=`).
test('UX MCP servers: the list, one server with its tools and approvals, sign-in', async ({ page }) => {
  const LIST = '/workspace/mcp-servers/personal/';
  const servers = (p: Page) => goto(p, LIST, (q) => q.getByText(SIGN_IN_SERVER, { exact: true }));
  await capture(page, 'mcp-servers', servers);
  await capture(page, 'mcp-tool-rules', async (p) => {
    await servers(p);
    await p.getByTestId('mcp-server-row').filter({ hasText: 'e2esaas' }).first().click();
    await expect(p).toHaveURL(/[?&]serverId=/);
    await expect(p.getByTestId('mcp-sign-in-card')).toBeVisible();
    await settle(p);
  });
  await capture(page, 'mcp-sign-in', async (p) => {
    await goto(p, `${LIST}?serverId=${encodeURIComponent(signInServerId)}`, (q) => q.getByTestId('mcp-sign-in-card'));
    await p.getByTestId('mcp-sign-in-card').getByRole('button', { name: 'Connect' }).click();
    await expect(p.getByRole('dialog')).toBeVisible();
    await settle(p);
  }, (p) => p.getByRole('dialog'));
});

test('UX erasure: delete a chat, delete the organization', async ({ page }) => {
  await capture(page, 'delete-chat', async (p) => {
    const conversationId = new URL(urls.answer).searchParams.get('conversationId') as string;
    await goto(p, urls.answer, answerActions);
    const sidebar = p.getByRole('button', { name: /^Open sidebar/ });
    if (await sidebar.isVisible()) await sidebar.click();
    await showRecents(p, conversationId);
    const row = p.locator(`a[href*="conversationId=${conversationId}"]`).first();
    await row.hover();
    await row.locator('xpath=..').getByRole('button', { name: 'More options' }).click();
    await p.getByRole('menuitem', { name: 'Delete' }).click();
    await expect(p.getByRole('dialog')).toBeVisible();
    await settle(p);
  }, (p) => p.getByRole('dialog'));
  await capture(page, 'delete-org', async (p) => {
    await goto(p, '/workspace/general/', (q) => q.getByTestId('delete-org-section'));
    await p.getByTestId('delete-org-section').getByRole('button', { name: 'Delete organization…' }).click();
    await expect(p.getByRole('alertdialog').or(p.getByRole('dialog'))).toBeVisible();
    await settle(p);
  }, (p) => p.getByRole('alertdialog').or(p.getByRole('dialog')));
});

test('UX settings: schedules, profile', async ({ page }) => {
  await capture(page, 'settings-schedules', (p) => goto(p, '/workspace/schedules/', (q) => q.getByRole('group', { name: SCHEDULE_NAME, exact: true })));
  await capture(page, 'settings-profile', (p) => goto(p, '/workspace/profile/', heading));
});

test('UX cleanup: the question is answered, so Needs you ends as it began', async ({ page }) => {
  // A question waits for its chat, not its run (ADR-INBOX-02 D4): there is no run to cancel, so the person answers it.
  await goto(page, urls.question, (q) => q.getByRole('radio', { name: 'North America' }));
  await page.getByRole('radio', { name: 'North America' }).click();
  await page.getByRole('button', { name: /^Submit$/ }).last().click();
  await page.goto(JOBS.needsYou);
  await expect(needsYouRows(page).filter({ hasText: 'Which region should the report cover?' })).toHaveCount(0, { timeout: 60_000 });
});

expectManifestLists('captures');
