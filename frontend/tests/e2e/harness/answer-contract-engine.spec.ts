/**
 * ANSWER-CONTRACT-1 (ADR-RUN-03), on the real engine path (`run.sh durable`, the `harness-engine` project): a new
 * default chat, as people use it, handing its work to the lane's engine on the scripted model. Nothing is mocked; every turn
 * reaches only the scripted model (`pinScriptedModel`).
 *
 * In a manual test, "Prepare a risk report for 'SAAS launch'" became a full report from a product FAQ and a runbook,
 * with no question, no word that nothing was found, and a model-picked "Medium". Here:
 * - AC-E1: the subject is not in the knowledge, so the question card is in the chat before any job starts; "Use what
 *   you can find" hands the work to a job, and the answer leads with the plain not-found line and a Low confidence.
 * - AC-E3: a job that asks mid-run: its question is the chat's own card, and the answer reaches the next job with its
 *   request, whose gateway answers the same question from it instead of asking again; the summary under the
 *   answered question is the answer's run, not "Waiting for your answer" (FU-2).
 * - FU-2: the not-found line is the chat's own note in the reader's language above the answer, with the English the
 *   answer text starts with left out, and the hand-off refused while the person is asked is no failed step.
 * - AC-E4 (FU-1): a subject that exists in records this lane indexed (uploaded through the KB API) runs with no
 *   question; its job searches and cites them, and the confidence is computed from that evidence (High), not a
 *   default.
 * - AC-E5 (FU-1): a subject only partly present gets the records that hold part of it as the card's options.
 */
import { randomBytes } from 'crypto';
import fs from 'fs';
import path from 'path';
import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { chatInput, controls, ScriptedModel, ScriptedSaas } from './support/lane';
import { LOOKS, useLook } from './support/classic-jobs';
import { createTestKb, deleteTestKb, uploadFileByApi, waitForIndexed } from '../knowledge-base/kb-upload.helpers';

test.describe.configure({ mode: 'serial' });

// The lane's default chat has the scripted SaaS's MCP server (e2esaas) connected, whose search the check runs too:
// the words name both places looked in (ANSWER-CONTRACT-SOURCES), never "in your knowledge" alone.
const PLACES = 'your PipesHub knowledge and e2esaas';
const NOT_FOUND = new RegExp(`I looked in ${PLACES} and couldn.t find anything about “SAAS launch”`);
const USE_FOUND = 'Use what you can find';

/** A turn on a new default chat (no agent): the chat most people use. */
async function sendDefaultTurn(page: Page, text: string): Promise<string> {
  await page.goto('/chat/');
  const input = chatInput(page);
  await input.waitFor({ timeout: 30_000 });
  await input.fill(text);
  await input.press('Enter');
  await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
  return new URL(page.url()).searchParams.get('conversationId') as string;
}

const questionCard = (page: Page): Locator => page.getByTestId('question-card').last();
const jobCards = (page: Page): Locator => page.getByTestId('job-card');

/** The steps of the newest reply, opened, for review (one picture, the default look). */
async function stepsShot(page: Page, name: string): Promise<void> {
  const dir = process.env.UI_CASE_SHOTS;
  if (!dir) return;
  await page.getByTestId('run-summary').last().scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(dir, `ac-${name}.png`) });
}

/** Skips the open card and waits for the answer it goes on to, so no card is left waiting. */
async function skipAndFinish(page: Page, done: string): Promise<void> {
  await questionCard(page).getByRole('button', { name: 'Skip', exact: true }).click();
  await expect(page.getByTestId('question-card')).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByText(done).last()).toBeVisible({ timeout: 300_000 });
  await expect.poll(async () => page.getByText(done).count(), { timeout: 300_000 }).toBeGreaterThanOrEqual(2);
}

async function answerCard(page: Page, option: string): Promise<void> {
  const card = questionCard(page);
  await card.getByText(option, { exact: true }).click();
  await page.locator('button').filter({ hasText: /^Submit$/ }).last().click();
}

/** One picture of the page as it is now, for review (no reload: a passing state). */
async function caseShot(page: Page, name: string): Promise<void> {
  const dir = process.env.UI_CASE_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `ac-${name}.png`) });
}

/** Pictures for review, when the lane names a folder (E2E_UI_CASE_SHOTS → UI_CASE_SHOTS). */
async function shots(page: Page, name: string): Promise<void> {
  const dir = process.env.UI_CASE_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  const url = page.url();
  for (const look of LOOKS) {
    await useLook(page, look);
    await page.goto(url);
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(dir, `ac-${name}-${look.name}.png`) });
  }
  await useLook(page, LOOKS[0]);
}

test('AC-E1: a subject that is not found is asked about before any job starts; going on leads with not-found and Low', async ({ page }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  const done = `The SAAS launch risk report ${tag} is ready.`;
  await sendDefaultTurn(page, [`Prepare a risk report for "SAAS launch" ${tag}`, `say ${done}`].join('\n'));

  const card = questionCard(page);
  await expect(card).toBeVisible({ timeout: 240_000 });
  await expect(card).toContainText(NOT_FOUND);
  await expect(card.getByText(USE_FOUND, { exact: true })).toBeVisible();
  // No job started: the card came first.
  await expect(jobCards(page)).toHaveCount(0);
  // FU-2: the hand-off refused while the person is asked first is part of asking, never a failed step.
  await expect(page.getByTestId('run-summary').last()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('run-summary').last()).not.toContainText('failed');
  // FU-5: a question is not an answer, so the card's turn shows no confidence (the old hard-coded "Medium").
  await expect(page.getByText('Medium', { exact: true })).toHaveCount(0);
  await shots(page, 'question');

  await answerCard(page, USE_FOUND);
  await expect(jobCards(page)).toHaveCount(1, { timeout: 240_000 });
  // The confidence is drawn only once the answer is final; the person's own message holds the scripted words too.
  await expect(page.getByText('Low', { exact: true }).last()).toBeVisible({ timeout: 300_000 });
  // FU-2: the chat's own note, worded from the typed fact; the answer no longer repeats the English line.
  const notice = page.getByTestId('not-found-notice').last();
  await expect(notice).toContainText(`I looked in ${PLACES} and couldn't find anything about “SAAS launch”.`);
  // What it draws on depends on what the stack holds (earlier cases index records): none, or related ones.
  await expect(notice).toContainText(/This answer is not based on any of your documents about it\.|related documents? that do(?:es)? not mention it/);
  await expect(page.getByTestId('chat-answer').last()).not.toContainText('couldn\'t find');
  await expect(page.getByText(done)).toHaveCount(2);
  await expect(page.getByText('Medium', { exact: true })).toHaveCount(0);
  await shots(page, 'answer');
});

test('AC-E3: a job that asks mid-run asks with the chat\'s card, and the answer reaches the next job', async ({ page }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  const question = 'Which launch do you mean?';
  const done = `The launch dates ${tag} are ready.`;
  await sendDefaultTurn(
    page,
    [
      `check the launch dates for me ${tag}`,
      `tool ask_user ${JSON.stringify({ question, options: ['Orbit GA', 'SAAS beta'] })}`,
      `say ${done}`,
    ].join('\n'),
  );

  const card = questionCard(page);
  await expect(card).toBeVisible({ timeout: 300_000 });
  await expect(card).toContainText(question);
  await expect(jobCards(page)).toHaveCount(1);
  await shots(page, 'job-question');

  await answerCard(page, 'Orbit GA');
  // The answer goes back to a job with the request; that job is answered from it and does not ask again.
  await expect(jobCards(page)).toHaveCount(2, { timeout: 300_000 });
  await expect(page.getByText(done)).toHaveCount(2, { timeout: 300_000 });
  // One question, answered once: no card is open and the one answer is "Orbit GA".
  await expect(page.getByTestId('question-card')).toHaveCount(0);
  await expect(page.getByText('You answered: Orbit GA')).toHaveCount(1);
  await shots(page, 'job-answered');
  // FU-2: the summary under the answered question is the answer's run, after a reload too.
  await page.reload();
  await page.getByTestId('activity-summary').last().click();
  const summary = page.getByTestId('run-summary').last();
  await expect(summary).toBeVisible({ timeout: 30_000 });
  await expect(summary).not.toContainText('Waiting for your answer');
  await expect(summary).toContainText('Answered');
  await stepsShot(page, 'job-answered-steps');
});

/** FU-1: records the lane indexes, uploaded through the KB API, that name the subject. */
async function indexedRecords(
  apiContext: import('@playwright/test').APIRequestContext,
  tag: string,
  records: Array<{ name: string; text: string }>,
): Promise<string> {
  const kb = await createTestKb(apiContext, `Answer contract ${tag}`);
  for (const record of records) {
    const id = await uploadFileByApi(kb.id, { name: record.name, mimeType: 'text/markdown', buffer: Buffer.from(record.text) });
    await waitForIndexed(apiContext, id);
  }
  return kb.id;
}

test('AC-E4 (FU-1): a subject that exists runs with no question; its job is grounded and its confidence is earned', async ({ page, apiContext }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  const subject = `Orbit${tag} launch`;
  const kbId = await indexedRecords(apiContext, tag, [
    { name: `orbit${tag}-plan.md`, text: `# ${subject} plan\n\nThe ${subject} goes live on 1 December; the finance review is due Friday.\n` },
    { name: `orbit${tag}-risks.md`, text: `# ${subject} risks\n\nThe ${subject} depends on the billing migration finishing first.\n` },
  ]);
  try {
    const search = JSON.stringify({ query: subject });
    const done = `The ${subject} risk report is ready`;
    await sendDefaultTurn(page, [
      `Prepare a risk report for "${subject}"`,
      `tool knowledgegraph__search ${search} || retrieval__search_internal_knowledge ${search}`,
      `say ${done} [plan](ref1) [risks](ref2).`,
    ].join('\n'));

    await expect(jobCards(page)).toHaveCount(1, { timeout: 240_000 });
    await expect(page.getByText(new RegExp(`${done}`)).last()).toBeVisible({ timeout: 300_000 });
    // No question was asked, and nothing says the subject was not found.
    await expect(page.getByTestId('question-card')).toHaveCount(0);
    await expect(page.getByTestId('not-found-notice')).toHaveCount(0);
    // Two records about it, both cited: High, computed from the evidence (ADR-RUN-03 D3), never a default Medium.
    await expect(page.getByText('High', { exact: true }).last()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText('Medium', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Low', { exact: true })).toHaveCount(0);
    await shots(page, 'found');
  } finally {
    await deleteTestKb(apiContext, kbId);
  }
});

test('AC-E5 (FU-1): a subject only partly present offers the records that hold part of it', async ({ page, apiContext }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  const record = `orbit${tag}-plan.md`;
  const kbId = await indexedRecords(apiContext, tag, [
    { name: record, text: `# Orbit${tag} launch plan\n\nThe Orbit${tag} launch goes live on 1 December.\n` },
  ]);
  try {
    // "rollout" is in no record: the one that holds "Orbit<tag>" is the near match.
    await sendDefaultTurn(page, [`Prepare a risk report for "Orbit${tag} rollout"`, `say The rollout report ${tag} is ready.`].join('\n'));
    const card = questionCard(page);
    await expect(card).toBeVisible({ timeout: 240_000 });
    await expect(card).toContainText(`I looked in ${PLACES} and couldn't find anything about “Orbit${tag} rollout”. Did you mean one of these?`);
    // The option is the record's name as the search shows it (no extension).
    await expect(card.getByText(`orbit${tag}-plan`, { exact: true })).toBeVisible();
    await expect(card.getByText(USE_FOUND, { exact: true })).toBeVisible();
    await expect(jobCards(page)).toHaveCount(0);
    await shots(page, 'near-miss');
    // No card is left waiting for the specs after this one (their Needs you counts are the org's).
    await skipAndFinish(page, `The rollout report ${tag} is ready.`);
  } finally {
    await deleteTestKb(apiContext, kbId);
  }
});

/**
 * ANSWER-CONTRACT-SKIP (found in a manual test: a skipped question was asked again and again): skipping
 * the grounding question goes on with what can be found, and the conversation never asks about that subject again,
 * whether the person skipped it or typed a new message instead. A request naming a different subject asks once.
 */
test('AC-E6 (SKIP): Skip goes on with what can be found; no second card; the answer leads with the not-found note', async ({ page }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  const done = `The skipped SAAS launch report ${tag} is ready.`;
  await sendDefaultTurn(page, [`Prepare a risk report for "SAAS launch" ${tag}`, `say ${done}`].join('\n'));
  const card = questionCard(page);
  await expect(card).toBeVisible({ timeout: 240_000 });
  await card.getByRole('button', { name: 'Skip', exact: true }).click();
  await expect(jobCards(page)).toHaveCount(1, { timeout: 240_000 });
  await expect(page.getByTestId('not-found-notice').last()).toContainText(`I looked in ${PLACES} and couldn't find anything about “SAAS launch”.`, { timeout: 300_000 });
  await expect(page.getByText('Low', { exact: true }).last()).toBeVisible();
  await expect(page.getByText(done)).toHaveCount(2);
  // The skipped question is never put again: no open card, after a reload too.
  await expect(page.getByTestId('question-card')).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(done)).toHaveCount(2, { timeout: 30_000 });
  await expect(page.getByTestId('question-card')).toHaveCount(0);
  await shots(page, 'skipped');
});

test('AC-E7 (SKIP): a new message instead of an answer is not asked the same thing again; a new subject is asked once', async ({ page }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  await sendDefaultTurn(page, [`Prepare a risk report for "SAAS launch" ${tag}`, `say The first report ${tag} is ready.`].join('\n'));
  await expect(questionCard(page)).toBeVisible({ timeout: 240_000 });

  // A new message while the card waits ("A new message skips it"), naming the same subject: no card again.
  const again = `The SAAS launch report ${tag} is written anyway.`;
  const input = chatInput(page);
  await input.fill([`Just write the risk report for "SAAS launch" ${tag}`, `say ${again}`].join('\n'));
  await input.press('Enter');
  await expect(page.getByText(again).last()).toBeVisible({ timeout: 300_000 });
  await expect(page.getByTestId('not-found-notice').last()).toContainText('“SAAS launch”', { timeout: 60_000 });
  await expect(page.getByTestId('question-card')).toHaveCount(0);

  // A new request naming a subject not asked about yet: asked once.
  const other = `Orbit${tag} GA`;
  await input.fill([`Prepare a risk report for "${other}"`, `say The ${other} report is ready.`].join('\n'));
  await input.press('Enter');
  const card = questionCard(page);
  await expect(card).toBeVisible({ timeout: 240_000 });
  await expect(card).toContainText(`I looked in ${PLACES} and couldn't find anything about “${other}”.`);
  await expect(page.getByTestId('question-card')).toHaveCount(1);
  await shots(page, 'new-subject');
  await skipAndFinish(page, `The ${other} report is ready.`);
});

/**
 * ANSWER-CONTRACT-SOURCES (found in a manual test: a connected MCP server was never searched): the check searches every connected source
 * the person can reach, picked by what its tools declare (a read-only search), not PipesHub knowledge alone.
 */
test('AC-E8 (SOURCES): a subject a connected MCP server\'s search finds runs with no question, and the job is told where', async ({ page }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  const subject = `Zephyr${tag} launch`;
  const l = controls();
  const saas = await ScriptedSaas.open(l);
  const model = await ScriptedModel.open(l);
  try {
    await saas.addDocument(`${subject} plan`, `The ${subject} goes live on 1 December; the finance review is due Friday.`);
    // The job's task names where it was found: the scripted model keeps no content, so a watched value says it
    // (ASCII only, as a client may escape the quote marks). With the task's list of connected sources (ADR-OWD-07
    // D-DISC-2) the fact is a mark on e2esaas's own entry, else its own line: both end with the tool that found it.
    const told = '(its `search_documents` tool)';
    await model.watchFor([told]);
    const done = `The ${subject} risk report is ready.`;
    await sendDefaultTurn(page, [`Prepare a risk report for "${subject}"`, `say ${done}`].join('\n'));
    await expect(jobCards(page)).toHaveCount(1, { timeout: 240_000 });
    // The answer is in (the person's own message holds the words too).
    await expect(page.getByText(done)).toHaveCount(2, { timeout: 300_000 });
    await expect(page.getByTestId('question-card')).toHaveCount(0);
    await expect(page.getByTestId('not-found-notice')).toHaveCount(0);
    expect((await model.sightings()).length, 'the job was told where the subject was found').toBeGreaterThan(0);
    await shots(page, 'found-in-mcp');
  } finally {
    await model.watchFor([]);
    await saas.close();
    await model.close();
  }
});

/**
 * CARDFAIL-1 (found in a manual test): the person's answers to a card got 503 RUN_CONFIG_UNAVAILABLE while the app
 * restarted, and the card stayed with no word, as if asked again. Here the answer's route is refused twice the way
 * it was in that test (the response the server answered there, with nothing written); the card says PipesHub is
 * restarting and retries by itself under the same turn id, and the answer lands once.
 */
test('AC-E9 (CARDFAIL): an answer refused while PipesHub restarts says so, retries by itself, and lands once', async ({ page }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  const done = `The restarted SAAS launch report ${tag} is ready.`;
  await sendDefaultTurn(page, [`Prepare a risk report for "SAAS launch" ${tag}`, `say ${done}`].join('\n'));
  await expect(questionCard(page)).toBeVisible({ timeout: 240_000 });

  const sent: string[] = [];
  const answerRoute = /\/api\/v1\/conversations\/[^/]+\/messages\/stream/;
  await page.route(answerRoute, async (route) => {
    sent.push(String((route.request().postDataJSON() as { runId?: string } | null)?.runId));
    if (sent.length <= 2) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE', message: 'The run configuration could not be resolved' } }),
      });
      return;
    }
    await route.continue();
  });
  await answerCard(page, USE_FOUND);
  const notice = page.getByTestId('card-send-notice');
  // Its icon is a ligature, so the words are matched, not the whole text.
  await expect(notice).toContainText("Couldn't send your answer: PipesHub is restarting. Trying again…", { timeout: 30_000 });
  await expect(notice).toHaveAttribute('data-phase', 'retrying');
  await caseShot(page, 'cardfail-retrying');
  await expect(jobCards(page)).toHaveCount(1, { timeout: 240_000 });
  await expect(notice).toHaveCount(0);
  await expect(page.getByText(done)).toHaveCount(2, { timeout: 300_000 });
  await page.unroute(answerRoute);
  expect(sent.length, 'two refused sends, then the one that landed').toBe(3);
  expect(new Set(sent).size, 'every send of the answer is the same turn').toBe(1);
  // Landed once: one job and one answer, after a reload too.
  await page.reload();
  await expect(page.getByText(done)).toHaveCount(2, { timeout: 30_000 });
  await expect(jobCards(page)).toHaveCount(1);
  await expect(page.getByTestId('question-card')).toHaveCount(0);
});
