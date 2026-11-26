/**
 * WP-89's chat outcome cases in the real page on a stack (case list: docs/agent-harness-v2/ui-cases/wp89-chat-outcome.json;
 * R19-FU-3). The scripted model cannot end a run cut short with a settled outcome, so each turn is replayed into the
 * real page as Node sends it: the turn's frames, then the saved conversation whose final text part carries
 * `runOutcome` (`projection/outcome-view.ts`). Nothing reaches a model.
 *
 *   WP89-1  cut short with the engine's file: the answer is the report's opening, the note names the file, Continue
 *   WP89-2  the file was not written, work kept: why, how many sources, that they were saved, Continue
 *   WP89-3  nothing gathered: never says anything was saved, Try again
 *   WP89-4  an answer saved before WP-89 (the presenter's English closing in it): rendered as saved, no second note
 *   WP89-6  a long command: its last 20 lines, the whole output and "Full log in Activity" one click away
 *
 * WP89-5 is a real turn on the lane, not a replay: its first model call reports `usage` prompt tokens, which the
 * ledger charges at the scripted model's price, and its next call is held, so the header is read while the run works.
 * WP89-7 needs engine runs: `wp89-in-flight-engine.spec.ts`.
 *
 * Shots with UI_CASE_SHOTS: `wp89-1` … `wp89-6` (support/case-shots.ts).
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { buildAguiConversation } from '../chat/agui-sse-builder';
import { json, MODEL_INFO } from '../chat/durable-conversation.mock';
import {
  activeRunId,
  cancelLeftRuns,
  chatInput,
  conversationPath,
  lane,
  script,
  ScriptedModel,
  sendTurn,
  untilHolding,
  type Lane,
} from './support/lane';
import { followSystemTheme, iconFonts, shoot } from './support/case-shots';
import { expectCardsFitAtEveryWidth } from './support/card-fit';
import { expectCardsReadableInBothThemes } from './support/card-contrast';

const frame = (type: string, fields: Record<string, unknown> = {}): string =>
  `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;

type Part = Record<string, unknown>;

/** The next turn the page sends, answered with `answer` streamed and the conversation saved with `parts`. */
async function replayTurn(page: Page, question: string, answer: string, parts: Part[]): Promise<void> {
  const id = `conv-wp89-${randomBytes(3).toString('hex')}`;
  const base = buildAguiConversation({ conversationId: id, userMessageId: `u-${id}`, question, modelInfo: MODEL_INFO, answer });
  const stored = { ...base, messages: base.messages.map((m) => (m['messageType'] === 'bot_response' ? { ...m, parts } : m)) };
  const frames =
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId: id } }) +
    (answer ? frame('TEXT_MESSAGE_START') + frame('TEXT_MESSAGE_CONTENT', { delta: answer }) + frame('TEXT_MESSAGE_END') : '') +
    frame('RUN_FINISHED', { result: { conversation: stored, meta: { requestId: id, duration: 1 } } });
  const turn = /\/api\/v1\/(agents\/[^/]+\/)?conversations\/stream$/;
  await page.route((url) => turn.test(url.pathname), (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: frames })
      : route.fallback(),
  );
  // The saved conversation names a made-up owner; the page's look-up of it would toast a 400 over the reply.
  await page.route('**/api/v1/users/by-ids', (route) => (route.request().method() === 'POST' ? json(route, []) : route.fallback()));
  await page.route(
    (url) => /\/api\/v1\/(agents\/[^/]+\/)?conversations\/[^/]+\/?$/.test(url.pathname) && url.pathname.includes(id),
    (route) =>
      route.request().method() === 'GET'
        ? json(route, { conversation: { ...stored, id, access: { isOwner: true, accessLevel: 'OWNER' } }, filters: {}, meta: {} })
        : route.fallback(),
  );
  await page.goto('/chat/');
  const input = chatInput(page);
  await input.waitFor({ timeout: 30_000 });
  await input.fill(question);
  await input.press('Enter');
}

const finalText = (content: string, runOutcome?: Record<string, unknown>): Part => ({
  type: 'text',
  content,
  isFinal: true,
  ...(runOutcome ? { runOutcome } : {}),
});

const notice = (page: Page) => page.getByTestId('outcome-notice').last();

test.beforeEach(async ({ page }) => {
  await followSystemTheme(page);
  await iconFonts(page);
});

test.describe('WP-89 the chat words a run’s outcome (replay)', () => {
  test('WP89-1: cut short with the engine’s file: its opening, the note naming the file, Continue', async ({ page }) => {
    const opening = '# Churn report\n\nChurn fell 2.1 points in Q3, led by the annual plans.';
    await replayTurn(page, `wp89-1 ${randomBytes(2).toString('hex')}`, opening, [
      finalText(opening, { kind: 'partial', reason: 'time', action: 'continue', file: { name: 'report.md', by: 'engine' }, sources: 3, kept: true, opening: true, closing: 'English' }),
    ]);
    await expect(notice(page)).toContainText('report.md holds what the task had written when it stopped', { timeout: 30_000 });
    await expect(notice(page)).toContainText('The full report is in report.md.');
    await expect(page.getByText('Churn fell 2.1 points in Q3').last()).toBeVisible();
    await expect(page.locator('body')).not.toContainText('This result is partial');
    await expect(page.getByTestId('outcome-action')).toHaveCount(1);
    await expect(page.getByTestId('outcome-action')).toHaveAttribute('data-action', 'continue');
    await expectCardsFitAtEveryWidth(page, () => notice(page), 'WP89-1 outcome notice');
    await expectCardsReadableInBothThemes(page, () => notice(page), 'WP89-1 outcome notice');
    await shoot(page, 'wp89-1', () => notice(page));
  });

  test('WP89-2: the file was not written, work kept: why, the sources, saved, Continue', async ({ page }) => {
    await replayTurn(page, `wp89-2 ${randomBytes(2).toString('hex')}`, '', [
      finalText('', { kind: 'partial', reason: 'time', action: 'continue', missing: 'report.md', sources: 21, kept: true, opening: false, closing: 'English' }),
    ]);
    await expect(notice(page)).toContainText("report.md isn't written yet.", { timeout: 30_000 });
    await expect(notice(page)).toContainText('Stopped early: time limit reached');
    await expect(notice(page)).toContainText('It read 21 sources.');
    await expect(notice(page)).toContainText("What it gathered is saved in this conversation's files.");
    await expect(page.getByTestId('outcome-action')).toContainText('Continue');
    await expectCardsFitAtEveryWidth(page, () => notice(page), 'WP89-2 outcome notice');
    await expectCardsReadableInBothThemes(page, () => notice(page), 'WP89-2 outcome notice');
    await shoot(page, 'wp89-2', () => notice(page));
  });

  test('WP89-3: nothing gathered: never says anything was saved, Try again', async ({ page }) => {
    await replayTurn(page, `wp89-3 ${randomBytes(2).toString('hex')}`, '', [
      finalText('', { kind: 'partial', reason: 'turns', action: 'retry', missing: 'report.md', sources: 0, kept: false, opening: false, closing: 'English' }),
    ]);
    await expect(notice(page)).toContainText('It stopped before it gathered anything.', { timeout: 30_000 });
    await expect(notice(page)).not.toContainText('saved');
    await expect(page.getByTestId('outcome-action')).toHaveAttribute('data-action', 'retry');
    await expect(page.getByTestId('outcome-action')).toContainText('Try again');
    await expectCardsFitAtEveryWidth(page, () => notice(page), 'WP89-3 outcome notice');
    await expectCardsReadableInBothThemes(page, () => notice(page), 'WP89-3 outcome notice');
    await shoot(page, 'wp89-3', () => notice(page));
  });

  test('WP89-4: an answer saved before WP-89 renders as saved, with no second note', async ({ page }) => {
    const saved = 'Findings so far: the Q3 numbers.\n\n---\n**This result is partial.** This stopped early when it reached its time limit.';
    await replayTurn(page, `wp89-4 ${randomBytes(2).toString('hex')}`, saved, [finalText(saved)]);
    const answer = page.getByText('This result is partial.').last();
    await expect(answer).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('outcome-notice')).toHaveCount(0);
    await shoot(page, 'wp89-4', () => answer);
  });

  test('WP89-6: a long command shows its last 20 lines, with the whole log one click away', async ({ page }) => {
    const output = Array.from({ length: 60 }, (_, n) => `tests/test_${String(n).padStart(2, '0')}.py::test_case PASSED`).join('\n');
    const answer = 'All 60 tests pass.';
    await replayTurn(page, `wp89-6 ${randomBytes(2).toString('hex')}`, answer, [
      { type: 'engine_command', command: 'pytest -q', status: 'completed', content: output, runId: 'run-wp89-6' },
      finalText(answer),
    ]);
    await expect(page.getByText(answer).last()).toBeVisible({ timeout: 30_000 });
    const summary = page.getByTestId('activity-summary').last();
    if ((await summary.getAttribute('aria-expanded')) === 'false') await summary.click();
    await page.getByText('pytest -q').last().click();
    const shown = page.getByTestId('engine-command-output').last();
    await expect(shown).toContainText('test_59.py');
    expect(((await shown.textContent()) ?? '').trim().split('\n')).toHaveLength(20);
    await expect(page.getByTestId('engine-command-full-log').last()).toBeVisible();
    await expectCardsFitAtEveryWidth(page, () => page.getByTestId('activity-summary').last(), 'WP89-6 activity');
    await expectCardsReadableInBothThemes(page, () => page.getByTestId('activity-summary').last(), 'WP89-6 activity');
    await shoot(page, 'wp89-6', () => shown);
  });
});

test.describe('WP-89 a running turn (the lane’s scripted model)', () => {
  let l: Lane;
  let model: ScriptedModel;

  test.beforeAll(async () => {
    l = lane();
    model = await ScriptedModel.open(l);
  });

  test.afterAll(async () => model.close());

  test.afterEach(async ({ apiContext }) => {
    await model.release();
    await cancelLeftRuns(apiContext);
  });

  test('WP89-5: while the run works the header shows its time and what it has spent so far, and not after', async ({
    page,
    apiContext,
  }) => {
    test.setTimeout(300_000);
    await model.reset();
    const done = `WP89-5 answered ${randomBytes(2).toString('hex')}.`;
    const conversationId = await sendTurn(
      page,
      l.toolAgentKey,
      script(
        'Find the ticket tools.',
        'usage 140000',
        `tool search_tools ${JSON.stringify({ query: 'ticket' })}`,
        'hold',
        `say ${done}`,
      ),
    );
    await untilHolding(model);
    const runId = await activeRunId(apiContext, l.toolAgentKey, conversationId);
    const header = page.getByTestId('activity-summary').last();
    await expect(header).toContainText(/\d+s · \$\d+\.\d{2} so far$/, { timeout: 60_000 });
    // The spend shown is the ledger's: the first call's prompt tokens at the scripted model's price.
    const cost = await apiContext.get(`${conversationPath(l.toolAgentKey, conversationId)}/runs/${runId}/cost`);
    expect(cost.ok(), `cost: ${cost.status()}`).toBe(true);
    const committed = ((await cost.json()) as { budget?: { committed?: { usdMicros?: number } } }).budget?.committed?.usdMicros ?? 0;
    expect(committed, 'the held run has spent money').toBeGreaterThan(0);
    const shown = /\$(\d+\.\d{2}) so far/.exec((await header.textContent()) ?? '')?.[1];
    expect(Math.abs(Number(shown) - committed / 1_000_000)).toBeLessThan(0.01);
    await expectCardsFitAtEveryWidth(page, () => header, 'WP89-5 running header');
    await expectCardsReadableInBothThemes(page, () => header, 'WP89-5 running header');
    await shoot(page, 'wp89-5', () => header);

    await model.release();
    await expect(page.getByText(done).last()).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId('activity-summary').last()).not.toContainText('so far');
  });
});
