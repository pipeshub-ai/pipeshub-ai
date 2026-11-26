/**
 * QAFINAL-FU-UX: an engine job's card and panel, captured as ux-shots.spec.ts captures the loop job's (desktop and
 * phone, light and dark, against the same baselines, each with an axe pass), on the real engine path: run.sh
 * durable-up's stack, the engine agent's turn handed to `code_task` and run by the lane's engine in a broker sandbox
 * on the scripted model (`ux-shots-engine` project; scripts/qa_full.py runs it in the ux lane on a stack of its own).
 *
 * Every text is fixed (the goal, the file, the answer), so what the captures show is the same every run on a fresh
 * stack; what changes with the clock is masked.
 */
import { test, expect } from '../fixtures/api-context.fixture';
import { script, ScriptedModel, sendTurn } from '../harness/support/lane';
import { engineAgentKey, engineLane } from '../harness/support/classic-jobs';
import { answerActions, jobCard, jobPanel, openSteps } from './classic';
import { capture, expectManifestLists, followEmulatedScheme, goto, settle } from './ux-capture';

test.describe.configure({ mode: 'serial' });

const FILE = 'report-q3.pdf';
const GOAL = 'Build the quarterly PDF report.';
const ANSWER = `The quarterly report is in ${FILE}.`;

let url = '';
let model: ScriptedModel;

test.beforeAll(async () => {
  model = await ScriptedModel.open(engineLane());
});
test.afterAll(async () => {
  await model?.close();
});

followEmulatedScheme();

test('UX engine setup: a code job that makes a file and ends Done', async ({ page }) => {
  // An engine run boots a sandbox: minutes, not seconds, on a loaded host.
  test.setTimeout(600_000);
  await model.reset();
  await sendTurn(page, engineAgentKey(), script(GOAL, `shell mkdir -p out && printf '%%PDF-1.4\\n%% Q3 report\\n%%%%EOF\\n' > out/${FILE}`, `say ${ANSWER}`));
  await expect(jobCard(page)).toBeVisible({ timeout: 240_000 });
  await expect(jobCard(page)).toHaveAttribute('data-status', 'done', { timeout: 300_000 });
  await expect(answerActions(page).last()).toBeVisible({ timeout: 120_000 });
  url = page.url();
});

test('UX engine jobs: the code job card and its panel', async ({ page }) => {
  test.setTimeout(300_000);
  // The card alone: where the chat is scrolled to is not the card's design, and a page capture moved by a line
  // between runs as the chat settled.
  await capture(page, 'engine-job-card', async (p) => {
    await goto(p, url, answerActions);
    await openSteps(p);
    await expect(jobCard(p)).toHaveAttribute('data-status', 'done');
    await settle(p);
  }, (p) => jobCard(p));
  await capture(page, 'engine-job-panel', async (p) => {
    await goto(p, url, answerActions);
    await openSteps(p);
    await jobCard(p).getByTestId('job-card-details').click();
    await expect(jobPanel(p)).toBeVisible({ timeout: 30_000 });
    await expect(jobPanel(p).getByTestId('job-panel-header')).toHaveAttribute('data-status', 'done');
    // An ended job opens on its Brief, which reads its outputs when the panel opens: wait for the file, then show the
    // panel from its top.
    await expect(jobPanel(p).getByTestId('job-brief-goal')).toBeVisible({ timeout: 30_000 });
    await expect(jobPanel(p).getByText(FILE).first()).toBeVisible({ timeout: 30_000 });
    await jobPanel(p).evaluate((root) => {
      for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) if (el.scrollTop > 0) el.scrollTop = 0;
    });
    await settle(p);
  }, jobPanel);
});

expectManifestLists('engineCaptures');
