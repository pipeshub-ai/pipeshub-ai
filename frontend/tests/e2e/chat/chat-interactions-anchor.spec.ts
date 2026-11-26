/**
 * chat-interactions-anchor.spec.ts — UXINT-1 (gallery §3, §4): a card is drawn under the timeline row of the call
 * that asked it, live and saved alike, so nothing jumps when the run ends or the page reloads; DOM order is journal
 * order. Also UXINT-4: the active card takes focus when the message box is empty, is announced, answers Enter and
 * Escape; and never takes focus from someone typing.
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';
import { evidenceFonts } from './uxint-replay';
import { FakeRunStream, json, MODEL_INFO, mockInFlightConversation, openConversation, type SseFrame } from './durable-conversation.mock';

const SHOTS = process.env.UXINT_SHOTS;
async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: test.info().outputPath(`${name}.png`) });
  if (SHOTS) {
    // Evidence only: the dev server's own overlay is not part of the page, and icons need their font.
    await page.addStyleTag({ content: 'nextjs-portal { display: none !important; }' });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `${SHOTS}/${name}.png` });
  }
}

const ANSWER = 'Both tickets are filed.';
type Spec = [string, Record<string, unknown>];
const ticket = { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false };

class TwoTickets {
  readonly decided: string[] = [];
  readonly stream: FakeRunStream;
  private readonly frames: SseFrame[];

  constructor(readonly sessionId: string, readonly runId: string) {
    const run = runId;
    const say = (id: string, text: string): Spec[] => [
      ['TEXT_MESSAGE_START', { messageId: id, role: 'assistant', runId: run }],
      ['TEXT_MESSAGE_CONTENT', { messageId: id, delta: text, runId: run }],
      ['TEXT_MESSAGE_END', { messageId: id, runId: run }],
    ];
    const call = (id: string, name: string, display: string, args: object): Spec[] => [
      ['TOOL_CALL_START', { toolCallId: id, toolCallName: name, displayName: display, runId: run }],
      ['TOOL_CALL_ARGS', { toolCallId: id, delta: JSON.stringify(args), runId: run }],
    ];
    const done = (id: string): Spec[] => [
      ['TOOL_CALL_END', { toolCallId: id, runId: run }],
      ['TOOL_CALL_RESULT', { toolCallId: id, messageId: `r-${id}`, content: 'ok', status: 'completed', runId: run }],
    ];
    const ask = (id: string, seq: number, turn: number, title: string): Spec[] => [
      ['CUSTOM', { name: 'approval_requested', value: { interactionId: `ix.approve.t${turn}.0`, toolCallId: id, toolName: 'e2esaas__create_ticket', displayName: 'E2Esaas · create_ticket', args: JSON.stringify({ title }), argsSummary: null, reason: ticket, seq, afterTurns: turn }, runId: run }],
      ['CUSTOM', { name: 'interaction_waiting', value: { count: 1, paused: false }, runId: run }],
      ['STATE_SNAPSHOT', { runId: run, snapshot: { hold: turn } }],
    ];
    const resolved = (turn: number): Spec[] => [
      ['CUSTOM', { name: 'approval_resolved', value: { interactionId: `ix.approve.t${turn}.0`, decision: 'approved' }, runId: run }],
      ['CUSTOM', { name: 'interaction_waiting', value: { count: 0, paused: false }, runId: run }],
    ];
    const specs: Spec[] = [
      ['RUN_STARTED', { threadId: sessionId, runId: run }],
      ...say('m1', 'Looking for the project.'),
      ...call('c1', 'e2esaas__search_projects', 'Searched projects', { q: 'wonders' }),
      ...done('c1'),
      ...say('m2', 'Filing the first ticket.'),
      ...call('c2', 'e2esaas__create_ticket', 'Ticket A', { title: 'Wonders deck A' }),
      ...ask('c2', 20, 2, 'Wonders deck A'),
      ...resolved(2),
      ...done('c2'),
      ...say('m3', 'Filing the second ticket.'),
      ...call('c3', 'e2esaas__create_ticket', 'Ticket B', { title: 'Wonders deck B' }),
      ...ask('c3', 30, 3, 'Wonders deck B'),
      ...resolved(3),
      ...done('c3'),
      ...say('m4', ANSWER),
      ['STATE_SNAPSHOT', { runId: run, snapshot: { final: true } }],
      ['RUN_FINISHED', { result: { conversation: this.stored(true), meta: { requestId: run, duration: 1 } } }],
    ];
    this.frames = specs.map(([type, data], i) => ({ id: `j:${i + 1}.1/0.0.0`, type, data }));
    this.stream = new FakeRunStream(() => this.frames);
    this.releaseHold(2);
  }

  private releaseHold(turn: number) {
    this.stream.released = this.frames.findIndex((f) => f.type === 'STATE_SNAPSHOT' && (f.data.snapshot as { hold?: number }).hold === turn) + 1;
  }

  /** The saved reply after the run: its parts, the approvals among them where the journal put them. */
  stored(finished: boolean) {
    const conversation = buildAguiConversation({ conversationId: this.sessionId, userMessageId: 'u1', question: 'File the two wonders tickets', modelInfo: MODEL_INFO, ...(finished ? { answer: ANSWER } : {}) });
    if (!finished) return conversation;
    const tool = (id: string, toolName: string, displayName: string) => ({ type: 'tool_call', toolCallId: id, toolName, displayName, status: 'completed', runId: this.runId });
    const approval = (id: string, turn: number, seq: number) => ({ type: 'approval', interactionId: `ix.approve.t${turn}.0`, toolCallId: id, toolName: 'e2esaas__create_ticket', displayName: 'E2Esaas · create_ticket', args: '{}', status: 'approved', seq, afterTurns: turn, runId: this.runId });
    const text = (content: string) => ({ type: 'text', content, runId: this.runId });
    const parts = [
      text('Looking for the project.'), tool('c1', 'e2esaas__search_projects', 'Searched projects'),
      text('Filing the first ticket.'), tool('c2', 'e2esaas__create_ticket', 'Ticket A'), approval('c2', 2, 20),
      text('Filing the second ticket.'), tool('c3', 'e2esaas__create_ticket', 'Ticket B'), approval('c3', 3, 30),
      { ...text(ANSWER), isFinal: true },
    ];
    const messages = conversation.messages.map((m) => (m['messageType'] === 'bot_response' ? { ...m, parts } : m));
    return { ...conversation, messages };
  }

  async mock(page: Page) {
    await mockInFlightConversation(page, { sessionId: this.sessionId, runId: this.runId, stream: this.stream, stored: () => this.stored(this.stream.finished) });
    await page.route(`**/api/v1/conversations/${this.sessionId}/runs/${this.runId}/interactions/authority`, (route) => json(route, { approve: true, deny: true }));
    await page.route(`**/api/v1/conversations/${this.sessionId}/runs/${this.runId}/followups*`, (route) => json(route, { suggestions: [] }));
    await page.route(`**/api/v1/conversations/${this.sessionId}/runs/${this.runId}/interactions/*/decision`, (route) => {
      const ix = decodeURIComponent(route.request().url().split('/interactions/')[1]?.split('/')[0] ?? '');
      this.decided.push(ix);
      if (ix === 'ix.approve.t2.0') this.releaseHold(3);
      else this.stream.releaseAll();
      return json(route, { receipt: { commandId: `c-${ix}` }, interaction: { interactionId: ix, decision: 'approved' } });
    });
  }
}

/** Labels of the rows and the cards, top to bottom, as the person reads them. */
async function readingOrder(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const marks = [...document.querySelectorAll('[data-testid="interaction"]')].map((el) => ({ y: el.getBoundingClientRect().top, label: `card:${el.getAttribute('data-state')}` }));
    const rows = ['Searched projects', 'Ticket A', 'Ticket B', 'Both tickets are filed.'].flatMap((text) => {
      // The last shown element with the text: the answer's words also sit in hidden helpers above it.
      const el = [...document.querySelectorAll('body *')]
        .filter((e) => e.children.length === 0 && e.textContent?.trim() === text && e.getBoundingClientRect().height > 0)
        .pop();
      return el ? [{ y: el.getBoundingClientRect().top, label: text }] : [];
    });
    return [...marks, ...rows].sort((a, b) => a.y - b.y).map((m) => m.label);
  });
}

test.describe('Chat — a card sits at its step (UXINT-1)', () => {
  test('live, after the run and after a reload, each card is under the row that asked it, in journal order', async ({ page }) => {
    test.setTimeout(120_000);
    const run = new TwoTickets('conv-uxintc-anchor', 'run-uxintc-anchor');
    await run.mock(page);
    await evidenceFonts(page);
    await openConversation(page, run.sessionId);

    await expect(page.locator('[data-interaction-active="true"]')).toHaveCount(1, { timeout: 30_000 });
    expect(await readingOrder(page)).toEqual(['Searched projects', 'Ticket A', 'card:active']);
    await shot(page, 'anchor-01-first-card');

    await page.getByRole('button', { name: /^Allow once: / }).click();
    await expect(page.getByTestId('interaction-heading')).toHaveText('Create ticket in E2Esaas?', { timeout: 30_000 });
    await expect.poll(() => readingOrder(page)).toEqual(['Searched projects', 'Ticket A', 'card:done', 'Ticket B', 'card:active']);
    await expect(page.getByTestId('interaction-receipt')).toHaveText(/Create ticket in E2Esaas · Allowed once by you/);
    await shot(page, 'anchor-02-receipt-then-second-card');

    await page.getByRole('button', { name: /^Allow once: / }).click();
    await expect(page.getByText(ANSWER, { exact: true })).toBeVisible({ timeout: 30_000 });
    // The steps the person decided in stay open: the receipts did not move.
    await expect.poll(() => readingOrder(page)).toEqual(['Searched projects', 'Ticket A', 'card:done', 'Ticket B', 'card:done', ANSWER]);
    await shot(page, 'anchor-03-final');

    await page.reload();
    await page.getByTestId('activity-summary').first().click();
    await expect.poll(() => readingOrder(page)).toEqual(['Searched projects', 'Ticket A', 'card:done', 'Ticket B', 'card:done', ANSWER]);
    await expect(page.getByTestId('activity-summary').first()).toContainText('2 approvals');
    await shot(page, 'anchor-04-after-reload');
  });
});

test.describe('Chat — keyboard and screen reader (UXINT-4)', () => {
  test('the active card takes focus when the box is empty, is announced, and answers Enter and Escape', async ({ page }) => {
    const run = new TwoTickets('conv-uxintc-keys', 'run-uxintc-keys');
    await run.mock(page);
    await evidenceFonts(page);
    await openConversation(page, run.sessionId);
    const heading = page.locator('[data-interaction-active="true"]').getByTestId('interaction-heading');
    await expect(heading).toBeFocused({ timeout: 30_000 });
    await expect(page.getByTestId('interaction-announcer')).toHaveText('Agent needs your approval: Create ticket in E2Esaas. 1 waiting for you.');
    await expect(page.getByTestId('interaction-announcer')).toHaveAttribute('aria-live', 'polite');
    await expect(page.locator('[data-interaction-active="true"]')).toHaveAttribute('role', 'region');
    await page.keyboard.press('Escape');
    await expect(page.locator('textarea.chat-input-textarea')).toBeFocused();
    await heading.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => run.decided).toEqual(['ix.approve.t2.0']);
    await shot(page, 'keys-01-enter-allowed');
  });

  test('a new card never takes focus from someone typing', async ({ page }) => {
    const run = new TwoTickets('conv-uxintc-typing', 'run-uxintc-typing');
    await run.mock(page);
    await evidenceFonts(page);
    await openConversation(page, run.sessionId);
    await expect(page.locator('[data-interaction-active="true"]')).toHaveCount(1, { timeout: 30_000 });
    await page.getByRole('button', { name: /^Allow once: / }).click();
    const box = page.locator('textarea.chat-input-textarea');
    await box.click();
    await box.pressSequentially('half a thought');
    await expect(page.locator('[data-interaction-active="true"]').getByTestId('interaction-heading')).toHaveText('Create ticket in E2Esaas?', { timeout: 30_000 });
    await expect(page.getByTestId('interaction-receipt')).toHaveCount(1);
    await expect(box).toBeFocused();
    await expect(box).toHaveAttribute('placeholder', 'Waiting for you above. Type to steer the run');
  });
});
