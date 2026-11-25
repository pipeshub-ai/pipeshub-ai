/**
 * QA-CLASSIC (was QA-AUTO's new-interface suite): what a person waits for in the classic chat, measured in the
 * browser on the harness lane's scripted model, and held to the budgets in latency-budgets.json (SLO.md, "Browser
 * latency"):
 *   - first token: Enter in the message box to the first text of the answer;
 *   - turn completion: Enter to the settled answer (its actions drawn);
 *   - approval round trip: Allow on the approval card to the answer the write leads to;
 *   - R30-LAT (QAFINAL-FU-LAT): the same first token and completion on the default chat (no agent), an MCP server's
 *     share of first token (an agent with one, the turn not using it, against the plain agent, back to back), and a
 *     question card's Submit to the answer the resumed run gives.
 * Times are the page's own clock (`performance.now()` in a capture-phase listener and a MutationObserver), so they
 * include the browser's work and none of the test runner's polling. The model is scripted, so the numbers are the
 * product's, not a provider's. One warm-up turn is not counted.
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, cancelLeftRuns, lane, script, ScriptedModel, ScriptedSaas, trackTurn, type Lane } from '../harness/support/lane';
import { answerActions, composer, discover, openAgentChat, openDefaultChat } from './classic';
import { BUDGETS, recordSamples } from './support';

// Not serial: a budget missed in one measure must not skip the other. scripts/qa_full.py runs them on one worker.
test.describe.configure({ mode: 'default' });

const TOOL = 'create_ticket';
// A long answer streams in several chunks, so first token and completion are apart. Its first word is in no title.
const FIRST = 'Zephyr';
const LAST = 'papa.';
const ANSWER = `${FIRST} bravo charlie delta echo foxtrot golf hotel india juliett kilo lima mike november oscar ${LAST}`;

let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;

test.beforeAll(async () => {
  l = lane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
});
test.afterAll(async () => {
  await model.close();
  await saas.close();
});
test.afterEach(async ({ apiContext }) => {
  await cancelLeftRuns(apiContext);
});

type Marks = { t0?: number; first?: number; done?: number };

/**
 * Starts the page's clock watch: `t0` at the trigger (Enter in the message box, or a click on an Allow or Submit button),
 * `first` when the answer holds `firstText`, `done` when it holds `doneText` and its actions are drawn.
 */
async function watch(page: Page, trigger: 'enter' | 'allow' | 'submit', firstText: string, doneText: string): Promise<void> {
  await page.evaluate(({ trigger, firstText, doneText }) => {
    const w = window as unknown as { __qa: Marks; __qaObserver?: MutationObserver };
    w.__qaObserver?.disconnect();
    w.__qa = {};
    // The script shows in the person's message, in the composer and its hidden mirror, and in the chat's title in
    // the sidebar (a link): text counts as the answer's only outside all of those.
    const NOT_ANSWER = 'a, textarea, [aria-hidden="true"], [data-testid^="user-query"]';
    const answered = (text: string) => {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if ((node.textContent ?? '').includes(text) && !node.parentElement?.closest(NOT_ANSWER)) return true;
      }
      return false;
    };
    const check = () => {
      if (w.__qa.t0 === undefined) return;
      if (w.__qa.first === undefined && answered(firstText)) w.__qa.first = performance.now();
      if (w.__qa.done === undefined && answered(doneText) && document.querySelector('[data-testid="message-actions"]')) {
        w.__qa.done = performance.now();
      }
    };
    if (trigger === 'enter') {
      document.addEventListener('keydown', (e) => { if (e.key === 'Enter' && w.__qa.t0 === undefined) w.__qa.t0 = performance.now(); }, { capture: true, once: true });
    } else {
      document.addEventListener('click', (e) => {
        const button = (e.target as Element | null)?.closest('button');
        const name = button?.getAttribute('aria-label') ?? button?.textContent ?? '';
        const pressed = trigger === 'allow' ? /^Allow/ : /^Submit$/;
        if (w.__qa.t0 === undefined && pressed.test(name.trim())) w.__qa.t0 = performance.now();
      }, { capture: true });
    }
    w.__qaObserver = new MutationObserver(check);
    w.__qaObserver.observe(document.body, { subtree: true, childList: true, characterData: true });
  }, { trigger, firstText, doneText });
}

async function marks(page: Page): Promise<Required<Marks>> {
  await expect.poll(() => page.evaluate(() => (window as unknown as { __qa: Marks }).__qa.done ?? null), { timeout: 90_000 }).not.toBeNull();
  return page.evaluate(() => (window as unknown as { __qa: Required<Marks> }).__qa);
}

function holdToBudget(stats: Record<string, { p50: number; p95: number }>, samples: Record<string, number[]>): void {
  for (const [metric, { p50, p95 }] of Object.entries(stats)) {
    const budget = BUDGETS.metrics[metric];
    expect.soft(p50, `${metric} p50 (samples ${JSON.stringify(samples[metric])})`).toBeLessThanOrEqual(budget.p50_ms);
    expect.soft(p95, `${metric} p95`).toBeLessThanOrEqual(budget.p95_ms);
  }
}

test('QA-LAT-1: first token and turn completion, on a new agent chat with the scripted model', async ({ page }) => {
  test.setTimeout(600_000);
  await model.reset();
  const n = BUDGETS.metrics.first_token_ms.samples;
  const first: number[] = [];
  const done: number[] = [];
  for (let i = 0; i <= n; i += 1) {
    await openAgentChat(page, l.agentKey);
    await watch(page, 'enter', FIRST, LAST);
    await composer(page).fill(script(`Spell the alphabet, sample ${i}.`, `say ${ANSWER}`));
    await composer(page).press('Enter');
    const m = await marks(page);
    await expect(page).toHaveURL(/conversationId=/);
    trackTurn(l.agentKey, new URL(page.url()).searchParams.get('conversationId') as string);
    await expect(answerActions(page).last()).toBeVisible();
    if (i === 0) continue;
    first.push(m.first - m.t0);
    done.push(m.done - m.t0);
  }
  const samples = { first_token_ms: first, turn_done_ms: done };
  holdToBudget(recordSamples(test.info(), 'turn', samples), samples);
});

test('QA-LAT-2: approval round trip, Allow to the answer after the write', async ({ page }) => {
  test.setTimeout(600_000);
  await model.reset();
  await saas.reset();
  const n = BUDGETS.metrics.approval_roundtrip_ms.samples;
  const roundTrips: number[] = [];
  for (let i = 0; i <= n; i += 1) {
    const done = `Ticket QA-LAT ${i} is filed.`;
    await openAgentChat(page, l.toolAgentKey);
    await composer(page).fill(script(`File a ticket titled QA-LAT ${i}.`, discover(TOOL),
      `tool ${TOOL} ${JSON.stringify({ title: `QA-LAT ${i}`, body: 'Latency sample.' })}`, `say ${done}`));
    await composer(page).press('Enter');
    await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
    trackTurn(l.toolAgentKey, new URL(page.url()).searchParams.get('conversationId') as string);
    const allow = allowOnceButton(page, TOOL);
    await expect(allow).toBeVisible({ timeout: 90_000 });
    await watch(page, 'allow', done, done);
    await allow.click();
    const m = await marks(page);
    if (i > 0) roundTrips.push(m.done - m.t0);
  }
  const samples = { approval_roundtrip_ms: roundTrips };
  holdToBudget(recordSamples(test.info(), 'approval', samples), samples);
  expect((await saas.effects()).length, 'each Allow ran its write once').toBe(n + 1);
});

/** One turn's first token and completion on a page already open: Enter to the answer's first text and its actions. */
async function timedTurn(page: Page, text: string, track: string): Promise<Required<Marks>> {
  await watch(page, 'enter', FIRST, LAST);
  await composer(page).fill(text);
  await composer(page).press('Enter');
  const m = await marks(page);
  await expect(page).toHaveURL(/conversationId=/);
  trackTurn(track, new URL(page.url()).searchParams.get('conversationId') as string);
  await expect(answerActions(page).last()).toBeVisible();
  return m;
}

test('QA-LAT-4: first token and turn completion on the default chat (/chat/, no agent)', async ({ page }) => {
  test.setTimeout(600_000);
  await model.reset();
  const n = BUDGETS.metrics.default_first_token_ms.samples;
  const first: number[] = [];
  const done: number[] = [];
  for (let i = 0; i <= n; i += 1) {
    await openDefaultChat(page);
    const m = await timedTurn(page, script(`Spell the alphabet on the default chat, sample ${i}.`, `say ${ANSWER}`), l.agentKey);
    if (i === 0) continue;
    first.push(m.first - m.t0);
    done.push(m.done - m.t0);
  }
  const samples = { default_first_token_ms: first, default_turn_done_ms: done };
  holdToBudget(recordSamples(test.info(), 'default-chat', samples), samples);
});

test("QA-LAT-5: an MCP server's share of first token, the ticket agent (its MCP server unused) against the plain agent", async ({ page }) => {
  test.setTimeout(900_000);
  await model.reset();
  const n = BUDGETS.metrics.mcp_first_token_ms.samples;
  const withMcp: number[] = [];
  const delta: number[] = [];
  // Back to back, the plain agent first: a pair shares the host's load of its moment, so the difference is the server's.
  for (let i = 0; i <= n; i += 1) {
    await openAgentChat(page, l.agentKey);
    const plain = await timedTurn(page, script(`Spell the alphabet, plain ${i}.`, `say ${ANSWER}`), l.agentKey);
    await openAgentChat(page, l.toolAgentKey);
    const mcp = await timedTurn(page, script(`Spell the alphabet, with a server ${i}.`, `say ${ANSWER}`), l.toolAgentKey);
    if (i === 0) continue;
    withMcp.push(mcp.first - mcp.t0);
    delta.push((mcp.first - mcp.t0) - (plain.first - plain.t0));
  }
  const samples = { mcp_first_token_ms: withMcp, mcp_first_token_delta_ms: delta };
  holdToBudget(recordSamples(test.info(), 'mcp', samples), samples);
});

test("QA-LAT-6: question round trip, a question card's Submit to the resumed run's answer", async ({ page }) => {
  test.setTimeout(600_000);
  await model.reset();
  const n = BUDGETS.metrics.question_roundtrip_ms.samples;
  const roundTrips: number[] = [];
  for (let i = 0; i <= n; i += 1) {
    const done = `Drawing the ancient seven, sample ${i}.`;
    const ask = `tool ask_user_question ${JSON.stringify({
      user_intent: 'Pick which wonders to draw',
      questions: [{ question: `Which set of wonders should I draw (${i})?`, options: [{ label: 'Ancient seven' }, { label: 'New seven (2007)' }], multiSelect: false }],
    })}`;
    await openAgentChat(page, l.agentKey);
    await composer(page).fill(script(`Draw the wonders, sample ${i}.`, ask, `say ${done}`));
    await composer(page).press('Enter');
    await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
    trackTurn(l.agentKey, new URL(page.url()).searchParams.get('conversationId') as string);
    await page.getByRole('radio', { name: 'Ancient seven' }).click({ timeout: 90_000 });
    const submit = page.getByRole('button', { name: /^Submit$/ }).last();
    await watch(page, 'submit', done, done);
    await submit.click();
    const m = await marks(page);
    if (i > 0) roundTrips.push(m.done - m.t0);
  }
  const samples = { question_roundtrip_ms: roundTrips };
  holdToBudget(recordSamples(test.info(), 'question', samples), samples);
});
