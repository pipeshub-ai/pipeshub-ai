/**
 * WP-10 / WP-18 / WP-15 in the real chat (case list: docs/agent-harness-v2/ui-cases/wp10-wp18-chat.json).
 *
 * Signed in to a running stack (the harness lane, or the human-test stack) with the scripted model. Cases the scripted model can drive
 * run live end to end: deny with a reason, Allow all for a batch, and links in an answer. Cases the scripted model
 * cannot produce are replayed into the real page as the frames Node sends (`mode: "replay"` in the case list): an
 * engine's approval preview (only a coding engine's held edit carries a diff), the five error codes, and a search
 * step that found a tool the person has not connected.
 *
 * With UI_CASE_SHOTS=<dir>, each case saves its pictures there: <case>-<light|dark>-<desktop|phone>.png.
 */
import { mkdirSync } from 'fs';
import { randomBytes } from 'crypto';
import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { buildAguiConversation } from '../chat/agui-sse-builder';
import { FakeRunStream, json, MODEL_INFO, mockInFlightConversation, openConversation, type SseFrame } from '../chat/durable-conversation.mock';
import {
  chatInput,
  denyButton,
  lane,
  noActiveRun,
  openFinishedActivity,
  script,
  ScriptedModel,
  ScriptedSaas,
  sendTurn,
  type Lane,
} from './support/lane';

test.describe.configure({ mode: 'serial' });

const SHOTS = process.env.UI_CASE_SHOTS ?? '';
const VIEWS = [
  { theme: 'light', view: 'desktop', size: { width: 1280, height: 900 } },
  { theme: 'dark', view: 'desktop', size: { width: 1280, height: 900 } },
  { theme: 'light', view: 'phone', size: { width: 375, height: 812 } },
  { theme: 'dark', view: 'phone', size: { width: 375, height: 812 } },
] as const;

/** The app follows the system theme, so emulating the colour scheme switches it without a reload. */
async function followSystemTheme(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('pipeshub-theme-preference', 'system');
    } catch {
      /* storage blocked: the default is the system theme too */
    }
  });
}

/**
 * Pictures only: the icon fonts come from Google Fonts, which a browser in a container sometimes fails to reach
 * (ERR_NETWORK_CHANGED); the runner fetches them instead, with retries, so icons show as icons, not their names.
 */
async function iconFonts(page: Page): Promise<void> {
  if (!SHOTS) return;
  await page.route(/fonts\.(googleapis|gstatic)\.com/, async (route) => {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        return await route.fulfill({ response: await route.fetch() });
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    return route.abort();
  });
}

/** The case's four pictures (light and dark, desktop and 375 px), each with `target` in view. */
async function shoot(page: Page, id: string, target: () => Locator): Promise<void> {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page
    .waitForFunction(() => document.fonts.check('24px "Material Icons Outlined"'), null, { timeout: 10_000 })
    .catch(() => undefined);
  for (const { theme, view, size } of VIEWS) {
    await page.setViewportSize(size);
    await page.emulateMedia({ colorScheme: theme });
    await target().scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    // Phone width works: nothing makes the page scroll sideways.
    if (view === 'phone') {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${id} fits 375 px`).toBe(true);
    }
    await page.screenshot({ path: `${SHOTS}/${id}-${theme}-${view}.png` });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ colorScheme: 'light' });
}

test.beforeEach(async ({ page }) => {
  await followSystemTheme(page);
  await iconFonts(page);
});

// ── Replayed approval cards (WP-10, C3.71) ───────────────────────────────────────────────────────────────────────

const SESSION = 'conv-wp10-cases';
const RUN = 'run-wp10-cases';
const IX = 'ix.approve.kwp10';
const QUESTION = 'Move the beta to Q4 in the launch plan';

const diffOf = (lines: string[]) => ['--- a/docs/launch-plan.md', '+++ b/docs/launch-plan.md', '@@ -1,6 +1,6 @@', ...lines, ''].join('\n');
const SHORT_DIFF = diffOf([
  ' ## Timeline',
  '-Beta opens in Q3 for design partners.',
  '+Beta opens in Q4 for design partners.',
  ' Owner: Ana Ruiz',
  '-GA: November',
  '+GA: January, after the security review',
]);
const LONG_DIFF = diffOf(Array.from({ length: 40 }, (_, i) => (i % 2 === 0 ? `-Milestone ${i}: old date` : `+Milestone ${i}: new date`)));

/** A coding engine's held edit, as the projector sends it (an engine approval line with its preview). */
function heldEdit(effect: Record<string, unknown>): SseFrame[] {
  const args = JSON.stringify({ file_path: 'docs/launch-plan.md', old_string: 'Q3', new_string: 'Q4' });
  const frames: [string, Record<string, unknown>][] = [
    ['RUN_STARTED', { threadId: SESSION, runId: RUN }],
    ['TOOL_CALL_START', { toolCallId: 'call-edit', toolCallName: 'github__edit_file', runId: RUN }],
    ['TOOL_CALL_ARGS', { toolCallId: 'call-edit', delta: args, runId: RUN }],
    [
      'CUSTOM',
      {
        name: 'approval_requested',
        value: {
          interactionId: IX,
          toolCallId: 'call-edit',
          toolName: 'github__edit_file',
          displayName: 'GitHub · edit_file',
          args,
          argsSummary: null,
          reason: { rule: 'engine_writes', sensitivity: 'public', sources: [], moreSources: 0, destination: 'acme/website', conversationGrant: false },
          effect,
          seq: 11,
          afterTurns: 1,
        },
        runId: RUN,
      },
    ],
    ['CUSTOM', { name: 'interaction_waiting', value: { count: 1, paused: false }, runId: RUN }],
    // Never released: the run still waits on the card.
    ['STATE_SNAPSHOT', { runId: RUN, snapshot: { final: true } }],
  ];
  return frames.map(([type, data], i) => ({ id: `j:${i + 1}.1/0.0.0`, type, data }));
}

async function openHeldEdit(page: Page, effect: Record<string, unknown>): Promise<unknown[]> {
  const decided: unknown[] = [];
  const stream = new FakeRunStream(() => heldEdit(effect));
  stream.releaseThrough((f) => f.type === 'CUSTOM' && f.data.name === 'interaction_waiting');
  const stored = () => buildAguiConversation({ conversationId: SESSION, userMessageId: 'u-wp10', question: QUESTION, modelInfo: MODEL_INFO });
  await mockInFlightConversation(page, { sessionId: SESSION, runId: RUN, stream, stored });
  await page.route(`**/api/v1/conversations/${SESSION}/runs/${RUN}/interactions/authority`, (route) => json(route, { approve: true, deny: true }));
  await page.route(`**/api/v1/conversations/${SESSION}/runs/${RUN}/interactions/*/decision`, (route) => {
    decided.push(route.request().postDataJSON());
    return json(route, { receipt: { commandId: 'c1' }, interaction: { interactionId: IX, decision: 'denied' } });
  });
  await openConversation(page, SESSION);
  return decided;
}

const editCard = (page: Page) => page.getByTestId('approval-card').filter({ has: page.getByTestId('approval-diff') }).last();

test.describe('WP-10 approval card: the concrete effect (replay)', () => {
  test('WP10-01: an engine edit shows its diff and the declared cost, not raw arguments', async ({ page }) => {
    await openHeldEdit(page, { diff: SHORT_DIFF, costEstimate: { usd: 0.04, basis: 'declared' } });
    const card = editCard(page);
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card.getByTestId('approval-diff-target')).toHaveText('docs/launch-plan.md');
    await expect(card.locator('[data-kind="add"]').first()).toHaveText('+Beta opens in Q4 for design partners.');
    await expect(card.locator('[data-kind="del"]').first()).toHaveText('-Beta opens in Q3 for design partners.');
    await expect(card.getByTestId('approval-cost')).toContainText('Estimated cost: about $0.04');
    await expect(card).not.toContainText('old_string');
    await shoot(page, 'wp10-01-diff-cost', () => card);
  });

  test('WP10-02: a long diff shows its first lines, opens to all of them, and says the worker cut it', async ({ page }) => {
    await openHeldEdit(page, { diff: LONG_DIFF, diffTruncated: true });
    const card = editCard(page);
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card.locator('[data-kind]')).toHaveCount(12);
    await expect(card.getByTestId('approval-diff-truncated')).toBeVisible();
    await shoot(page, 'wp10-02-long-diff-folded', () => card);
    await card.getByRole('button', { name: /Show the full change \(41 lines\)/ }).click();
    await expect(card.locator('[data-kind]')).toHaveCount(41);
    await shoot(page, 'wp10-02-long-diff-open', () => card.getByTestId('approval-diff-truncated'));
  });
});

// ── Live: deny with a reason, Allow all (WP-10, C3.72) and links (WP-15) ─────────────────────────────────────────

const TOOL = 'create_ticket';
let l: Lane | null = null;
let model: ScriptedModel | null = null;
let saas: ScriptedSaas | null = null;

test.describe('WP-10 approvals and WP-15 links on the real stack (live)', () => {
  test.beforeAll(async () => {
    l = lane();
    model = await ScriptedModel.open(l);
    saas = await ScriptedSaas.open(l);
  });
  test.afterAll(async () => {
    await model?.close();
    await saas?.close();
  });
  test.beforeEach(async () => {
    await model?.reset();
    await saas?.reset();
  });
  test.afterEach(async ({ page }) => {
    const stop = page.getByRole('button', { name: 'Stop generating' });
    if (!page.isClosed() && (await stop.isVisible().catch(() => false))) await stop.click();
  });

  test('WP10-03: Deny opens the reason field; the reason is sent with the denial and the receipt quotes it', async ({ page, apiContext }) => {
    const tag = randomBytes(4).toString('hex');
    const reason = 'Post it in #team-updates, not #general';
    const conversationId = await sendTurn(
      page,
      l!.toolAgentKey,
      script(
        `wp10 deny ${tag}`,
        `tool search_tools ${JSON.stringify({ query: TOOL, make_visible: true })}`,
        `tool ${TOOL} ${JSON.stringify({ title: `Launch moves ${tag}`, body: 'Launch moves to January.' })}`,
        'say Understood, I did not file it.',
      ),
    );
    await denyButton(page, TOOL).click({ timeout: 90_000 });
    const field = page.getByTestId('deny-reason');
    await expect(field).toBeVisible();
    await page.getByTestId('deny-reason-input').fill(reason);
    await expect(field).toContainText(`${reason.length} / 1000`);
    await shoot(page, 'wp10-03-deny-reason-open', () => field);
    const posted = page.waitForRequest((r) => r.method() === 'POST' && /\/interactions\/[^/]+\/decision$/.test(new URL(r.url()).pathname));
    await page.getByTestId('deny-confirm').click();
    expect((await posted).postDataJSON()).toMatchObject({ decision: 'denied', reason });
    // The finished turn is redrawn from the saved conversation, so the receipt quotes the reason the projection kept.
    const receipt = () => page.getByTestId('interaction-receipt').filter({ hasText: reason }).last();
    await noActiveRun(apiContext, l!.toolAgentKey, conversationId);
    await expect(receipt()).toContainText(`Denied by you, because “${reason}”`, { timeout: 30_000 });
    expect(await saas!.effects()).toEqual([]);
    // The saved conversation keeps the reason (the denial's journaled answerRef), so a reload quotes it too.
    await page.reload();
    await openFinishedActivity(page);
    await expect(receipt()).toContainText(`Denied by you, because “${reason}”`, { timeout: 60_000 });
    await shoot(page, 'wp10-03-deny-reason-receipt', receipt);
  });

  test('WP10-04: a batch of three is one card; Allow all 3 runs all three', async ({ page, apiContext }) => {
    const tag = randomBytes(4).toString('hex');
    const tickets = ['one', 'two', 'three'].map((n) => ({ title: `batch ${n} ${tag}`, body: 'WP10-04' }));
    const conversationId = await sendTurn(
      page,
      l!.toolAgentKey,
      script(
        `wp10 allow all ${tag}`,
        `tool search_tools ${JSON.stringify({ query: TOOL, make_visible: true })}`,
        `tools ${TOOL} ${JSON.stringify(tickets)}`,
        'say Filed all three.',
      ),
    );
    const group = page.getByTestId('approval-group').filter({ has: page.getByRole('button', { name: 'Allow all 3' }) });
    await expect(group).toHaveCount(1, { timeout: 90_000 });
    await expect(group.getByTestId('approval-group-item')).toHaveCount(3);
    await shoot(page, 'wp10-04-allow-all-grouped', () => group);
    await group.getByRole('button', { name: 'Allow all 3' }).click();
    await expect(page.getByText('Filed all three.', { exact: true })).toBeVisible({ timeout: 90_000 });
    await noActiveRun(apiContext, l!.toolAgentKey, conversationId);
    expect((await saas!.effects()).map((e) => e.tool)).toEqual([TOOL, TOOL, TOOL]);
  });

  test('WP15-01: a plain link opens directly; a link whose address carries data asks first and names the host', async ({ page }) => {
    const tag = randomBytes(4).toString('hex');
    await sendTurn(
      page,
      l!.agentKey,
      script(
        `wp15 links ${tag}`,
        'say See [the release notes](https://docs.acme.dev/release-notes) and [the report](https://reports.example.net/view?user=ana%40acme.dev&token=abc123).',
      ),
    );
    const plain = page.getByRole('link', { name: 'the release notes' }).last();
    await expect(plain).toBeVisible({ timeout: 90_000 });
    await expect(plain).toHaveAttribute('href', 'https://docs.acme.dev/release-notes');
    await expect(plain).not.toHaveAttribute('data-link-carries-data', '');
    await shoot(page, 'wp15-01-link-normal', () => plain);

    const flagged = page.locator('a[data-link-carries-data]').filter({ hasText: 'the report' }).last();
    // The visible address leaks nothing: it is the link without its data.
    await expect(flagged).toHaveAttribute('href', 'https://reports.example.net/view');
    // The streamed answer is drawn again as the saved message when the stream ends, which drops a confirm opened on
    // the streamed copy: a click in that last moment opened nothing (gate 1e7ca64f69).
    await expect(page.getByRole('button', { name: 'Stop generating' })).toHaveCount(0, { timeout: 90_000 });
    await expect(page.getByRole('button', { name: 'Regenerate' }).last()).toBeVisible({ timeout: 30_000 });
    await flagged.click();
    const popover = page.getByRole('dialog').filter({ hasText: 'This link sends extra data' });
    await expect(popover).toContainText('reports.example.net');
    await expect(popover.getByRole('link', { name: 'Open link' })).toHaveAttribute(
      'href',
      'https://reports.example.net/view?user=ana%40acme.dev&token=abc123',
    );
    await shoot(page, 'wp15-02-link-flagged-query', () => popover);
  });
});

// ── Replayed errors and the Connect row (WP-18, C10.02) ──────────────────────────────────────────────────────────

const frame = (type: string, fields: Record<string, unknown> = {}): string =>
  `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;

/**
 * The next turn the page sends, answered as the server would: an HTTP refusal (admission) or a stream of frames
 * that ends the turn. A stream names a conversation, so its saved form is served too.
 */
async function answerNextTurn(
  page: Page,
  reply: { status: number; code: string; message: string } | { frames: string; stored: ReturnType<typeof buildAguiConversation> },
): Promise<void> {
  const turn = /\/api\/v1\/(agents\/[^/]+\/)?conversations\/stream$/;
  await page.route((url) => turn.test(url.pathname), (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    if ('status' in reply) {
      return route.fulfill({
        status: reply.status,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: reply.code, message: reply.message } }),
      });
    }
    return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: reply.frames });
  });
  if (!('frames' in reply)) return;
  const id = reply.stored._id;
  // The stored conversation names a made-up owner; the page's look-up of it would toast a 400 over the reply.
  await page.route('**/api/v1/users/by-ids', (route) => (route.request().method() === 'POST' ? json(route, []) : route.fallback()));
  await page.route(
    (url) => /\/api\/v1\/(agents\/[^/]+\/)?conversations\/[^/]+\/?$/.test(url.pathname) && url.pathname.includes(id),
    (route) =>
      route.request().method() === 'GET'
        ? json(route, { conversation: { ...reply.stored, id, access: { isOwner: true, accessLevel: 'OWNER' } }, filters: {}, meta: {} })
        : route.fallback(),
  );
}

async function ask(page: Page, question: string): Promise<void> {
  await page.goto('/chat/');
  const input = chatInput(page);
  await input.waitFor({ timeout: 30_000 });
  await input.fill(question);
  await input.press('Enter');
}

/** A turn whose run ends with `RUN_ERROR` (a run ending's code). */
function runError(code: string, message: string, question: string) {
  const id = `conv-${code}-${randomBytes(3).toString('hex')}`;
  const stored = buildAguiConversation({ conversationId: id, userMessageId: `u-${id}`, question, modelInfo: MODEL_INFO });
  return {
    stored,
    frames: frame('CUSTOM', { name: 'conversation_created', value: { conversationId: id } }) + frame('RUN_ERROR', { code, message }),
  };
}

/** A turn that ends with an error answer (its code saved on the conversation's error message). */
function errorAnswer(code: string, question: string) {
  const id = `conv-${code}-${randomBytes(3).toString('hex')}`;
  const base = buildAguiConversation({ conversationId: id, userMessageId: `u-${id}`, question, modelInfo: MODEL_INFO });
  const now = new Date().toISOString();
  const stored = {
    ...base,
    messages: [
      ...base.messages,
      { _id: `e-${id}`, messageType: 'error', content: 'worker text', errorCode: code, citations: [], createdAt: now, updatedAt: now },
    ],
  };
  return {
    stored,
    frames:
      frame('CUSTOM', { name: 'conversation_created', value: { conversationId: id } }) +
      frame('STATE_SNAPSHOT', { snapshot: { final: true, answerMatchType: 'Error', errorCode: code } }) +
      frame('RUN_FINISHED', { result: { conversation: stored, meta: { requestId: id, duration: 1 } } }),
  };
}

test.describe('WP-18 error words and next steps (replay)', () => {
  const CASES = [
    {
      id: 'wp18-01-access-policy-unavailable',
      title: 'WP18-01: ACCESS_POLICY_UNAVAILABLE says to wait, never to pick another model, with Try again',
      reply: () => ({ status: 503, code: 'HTTP_AGENT_HARNESS_ACCESS_POLICY_UNAVAILABLE', message: 'policy store down' }),
      words: "Your organization's AI rules couldn't be read just now, so nothing was sent. Wait a moment, then send it again.",
      button: 'Try again',
      never: /pick another model/i,
    },
    {
      id: 'wp18-02-session-erased',
      title: 'WP18-02: SESSION_ERASED says the conversation was deleted, with New chat',
      reply: () => ({ status: 410, code: 'HTTP_AGENT_HARNESS_SESSION_ERASED', message: 'erased' }),
      words: 'This conversation was deleted. Start a new chat to keep going.',
      button: 'New chat',
    },
    {
      id: 'wp18-03-gvisor-unavailable',
      title: 'WP18-03: gvisor_unavailable says to ask the admin, with the guide and no button',
      reply: (q: string) => runError('gvisor_unavailable', 'gVisor required', q),
      words: 'Coding engines need the gVisor sandbox runtime on this server. Ask your admin to install it',
      button: null,
    },
    {
      id: 'wp18-04-budget-exhausted',
      title: 'WP18-04: budget_exhausted says to ask the admin or ask for less, no button',
      reply: (q: string) => errorAnswer('budget_exhausted', q),
      words: 'This reply stopped because it reached its spending limit. Ask your admin to raise the limit, or ask for less at a time.',
      button: null,
    },
    {
      id: 'wp18-05-budget-insufficient',
      title: 'WP18-05: budget_insufficient says to ask the admin or ask for less, no button',
      reply: (q: string) => errorAnswer('budget_insufficient', q),
      words: 'This reply stopped because its spending limit had too little left for the next step. Ask your admin to raise the limit, or ask for less at a time.',
      button: null,
    },
  ] as const;

  for (const c of CASES) {
    test(c.title, async ({ page }) => {
      const question = `${c.id} ${randomBytes(3).toString('hex')}`;
      await answerNextTurn(page, c.reply(question));
      await ask(page, question);
      const reply = page.getByText(c.words, { exact: false }).last();
      await expect(reply).toBeVisible({ timeout: 30_000 });
      if ('never' in c) await expect(page.locator('body')).not.toContainText(c.never);
      const step = page.getByTestId('error-next-step');
      if (c.button) {
        await expect(step.getByRole('button', { name: c.button })).toBeVisible();
      } else {
        await expect(step).toHaveCount(0);
      }
      await shoot(page, c.id, () => (c.button ? step : reply));
    });
  }

  test('WP18-06: a search that found a tool the person has not connected shows a Connect row', async ({ page }) => {
    const question = `wp18 connect ${randomBytes(3).toString('hex')}`;
    const id = `conv-connect-${randomBytes(3).toString('hex')}`;
    const result = JSON.stringify({
      matches: [],
      unavailable: [
        { name: 'jira__create_issue', toolset: 'jira', description: 'Create a Jira issue.', reason: 'not_authenticated' },
        { name: 'goldencrm__close_deal', toolset: 'goldencrm', description: 'Close a deal.', reason: 'not_attached' },
      ],
      message: 'No attached tool matched your query, but a matching tool exists.',
    });
    const answer = "I can't create the Jira issue until Jira is connected for your account.";
    const base = buildAguiConversation({ conversationId: id, userMessageId: `u-${id}`, question, modelInfo: MODEL_INFO, answer });
    const parts = [
      { type: 'tool_call', toolCallId: 'call-find', toolName: 'search_tools', args: '{"query": "create issue"}', status: 'completed', resultPreview: result, resultSummary: 'Found 0 matches' },
      { type: 'text', content: answer, isFinal: true },
    ];
    const stored = { ...base, messages: base.messages.map((m) => (m['messageType'] === 'bot_response' ? { ...m, parts } : m)) };
    const frames =
      frame('CUSTOM', { name: 'conversation_created', value: { conversationId: id } }) +
      frame('TOOL_CALL_START', { toolCallId: 'call-find', toolCallName: 'search_tools' }) +
      frame('TOOL_CALL_ARGS', { toolCallId: 'call-find', delta: '{"query": "create issue"}' }) +
      frame('CUSTOM', { name: 'tool_unavailable', value: { tool: 'create_issue', toolset: 'jira', reason: 'not_authenticated', message: 'Jira is not connected.' } }) +
      frame('TOOL_CALL_RESULT', { toolCallId: 'call-find', content: result, status: 'completed' }) +
      frame('TEXT_MESSAGE_START') +
      frame('TEXT_MESSAGE_CONTENT', { delta: answer }) +
      frame('TEXT_MESSAGE_END') +
      frame('RUN_FINISHED', { result: { conversation: stored, meta: { requestId: id, duration: 1 } } });
    await answerNextTurn(page, { frames, stored });
    await ask(page, question);
    await expect(page.getByText(answer).last()).toBeVisible({ timeout: 30_000 });
    // A finished turn keeps its steps in a section that starts collapsed.
    const summary = page.getByTestId('activity-summary').last();
    if ((await summary.count()) > 0 && (await summary.getAttribute('aria-expanded')) === 'false') await summary.click();
    const row = page.getByTestId('tool-unavailable-row').filter({ hasText: 'Jira · Create issue' }).last();
    await expect(row).toContainText("Jira · Create issue isn't connected");
    await expect(row.getByRole('link', { name: 'Connect Jira · Create issue' })).toHaveAttribute(
      'href',
      '/workspace/actions/personal/?toolsetType=jira',
    );
    await expect(page.getByTestId('tool-unavailable-row').filter({ hasText: 'Goldencrm' }).last()).toContainText(
      "isn't available to this agent",
    );
    await expect(page.locator('body')).not.toContainText('Jira is not connected.');
    await shoot(page, 'wp18-06-tool-unavailable-connect', () => row);
  });
});
