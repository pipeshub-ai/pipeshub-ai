/**
 * QA-CLASSIC journey J2 (was QA-AUTO's new-interface code job journey): code job → Jobs → Needs you → files, on
 * the real engine path (`harness-engine`, run.sh durable: the lane's engine, E2E_ENGINE, in a broker sandbox, the scripted model and
 * SaaS). The person asks the engine agent for a table and a ticket; the job card appears in the chat; the Jobs page
 * lists the job while it waits on them, and Needs you lists its ask with the job's name; Review in chat lands on
 * the ask; Allow runs the write once; the job ends Done, and the Jobs page lists it under Finished; its Open link
 * brings back the chat with the job's panel, whose Outputs list the file; and Download gives the file's bytes as the
 * job wrote them. Nothing is mocked.
 *
 * CJ-5 checks each surface's states on its own; this is the one story across them.
 */
import { randomBytes } from 'crypto';
import { readFileSync } from 'fs';
import type { APIRequestContext } from '@playwright/test';
import { request as playwrightRequest } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, cancelLeftRuns, conversationPath, sendTurn } from '../harness/support/lane';
import { jobCard, jobPanel, jobRow, JOBS, jobsSection, needsYouEntry, needsYouRows } from './classic';

test.describe.configure({ mode: 'serial' });

const AGENT_KEY = process.env.E2E_ENGINE_AGENT_KEY ?? '';
const LLM_URL = process.env.E2E_LLM_URL;
const SAAS_URL = process.env.E2E_SAAS_URL;
const CONTROL = process.env.E2E_LLM_CONTROL ?? '';
const TOOL = 'create_ticket';

let model: APIRequestContext;
let saas: APIRequestContext;

const control = (baseURL: string | undefined) => playwrightRequest.newContext({ baseURL, extraHTTPHeaders: { 'X-E2E-Control': CONTROL } });
const effects = async () => ((await (await saas.get('/__control/effects')).json()) as { effects: Array<{ tool: string }> }).effects;

test.beforeAll(async () => {
  expect(AGENT_KEY && LLM_URL && SAAS_URL, 'needs the durable engine stack (deployment/e2e/run.sh durable)').toBeTruthy();
  model = await control(LLM_URL);
  saas = await control(SAAS_URL);
  await model.post('/__control/reset');
  await saas.post('/__control/reset');
});
test.afterAll(async () => {
  await model?.dispose();
  await saas?.dispose();
});
test.afterEach(async ({ apiContext }) => {
  await cancelLeftRuns(apiContext);
});

/** The engine child the turn handed its script to (CTL-6): the turn's first child run. */
async function handedOffJob(api: APIRequestContext, conversationId: string): Promise<string> {
  let job: string | undefined;
  await expect
    .poll(async () => {
      const read = (await (await api.get(conversationPath(AGENT_KEY, conversationId))).json()) as { conversation?: { activeRunId?: string } };
      const turn = read.conversation?.activeRunId;
      if (!turn) return null;
      const timeline = (await (await api.get(`${conversationPath(AGENT_KEY, conversationId)}/runs/${turn}/timeline`)).json()) as { children?: Array<{ runId: string }> };
      job = timeline.children?.[0]?.runId;
      return job ?? null;
    }, { message: 'the turn handed its work to a job', timeout: 240_000 })
    .not.toBeNull();
  return job as string;
}

test('QA-J2: a code job waits on the person in Jobs and Needs you, runs its write on Allow, and hands back its file', async ({ page, apiContext }) => {
  test.setTimeout(900_000);
  const tag = randomBytes(3).toString('hex');
  const file = `q3-${tag}.csv`;
  const ticket = `QAJ2 ${tag}`;
  const goal = `Write the Q3 table to ${file}, then file a ticket ${ticket}.`;
  const content = 'region,revenue\nnorth,120\n';
  const conversationId = await sendTurn(page, AGENT_KEY, [
    goal,
    `shell mkdir -p out && printf 'region,revenue\\nnorth,120\\n' > out/${file}`,
    `tool ${TOOL} ${JSON.stringify({ title: ticket, body: 'From QA-J2.' })}`,
    `say The Q3 table is in ${file} and ticket ${ticket} is filed.`,
  ].join('\n'));
  await expect(jobCard(page)).toBeVisible({ timeout: 240_000 });
  const jobRunId = await handedOffJob(apiContext, conversationId);
  await expect(allowOnceButton(page, TOOL).first()).toBeVisible({ timeout: 240_000 });
  await expect(jobCard(page)).toHaveAttribute('data-status', 'needsYou', { timeout: 60_000 });

  // Away from the chat: the Jobs page lists the job as needing the person; Needs you names its ask and the job.
  await page.goto(JOBS.all);
  await expect(jobsSection(page, 'Needs you').locator(`[data-run-id="${jobRunId}"]`)).toBeVisible({ timeout: 60_000 });
  await needsYouEntry(page).click();
  const ask = needsYouRows(page).and(page.locator(`[data-run-id="${jobRunId}"]`));
  await expect(ask).toHaveCount(1, { timeout: 30_000 });
  // Its title worded as the job card's approval words it (QA-CLASSIC-FU-2), never by the tool's id; its meta line
  // repeats the job's and the chat's titles, which here are the scripted message that names the tool.
  await expect(ask.getByText(/^Create ticket( in \S+)?\?$/)).toBeVisible();
  await expect(ask).not.toContainText(`· ${TOOL}`);
  await expect(ask).not.toContainText('Approve:');
  expect(await effects(), 'nothing runs while it waits').toEqual([]);
  await ask.getByRole('link', { name: 'Review in chat' }).click();
  await expect(page).toHaveURL(new RegExp(`conversationId=${conversationId}`));
  await expect(page).toHaveURL(new RegExp(`job=${jobRunId}`));

  // Decide: Allow runs the write once; the job ends Done.
  const allow = allowOnceButton(page, TOOL).first();
  await expect(allow).toBeVisible({ timeout: 60_000 });
  await allow.click();
  await expect(jobCard(page)).toHaveAttribute('data-status', 'done', { timeout: 300_000 });
  expect((await effects()).map((e) => e.tool)).toEqual([TOOL]);

  // The Jobs page lists it under Finished; Open brings back its chat with the job's panel.
  await page.goto(JOBS.all);
  const row = jobsSection(page, 'Finished').locator(`[data-run-id="${jobRunId}"]`);
  await expect(row).toBeVisible({ timeout: 60_000 });
  await jobRow(page, jobRunId).getByRole('link', { name: /^Open job: / }).click();
  await expect(page).toHaveURL(new RegExp(`conversationId=${conversationId}.*job=${jobRunId}|job=${jobRunId}.*conversationId=${conversationId}`));
  const panel = jobPanel(page);
  await expect(panel).toBeVisible({ timeout: 60_000 });
  await expect(panel.getByTestId('job-panel-header')).toHaveAttribute('data-status', 'done');

  // Its Outputs tab shows the file as the answer's file card (BGUI-11), and Download gives its bytes.
  await panel.getByRole('tablist', { name: 'Job sections' }).getByRole('tab', { name: /^Outputs( \d+)?$/ }).click();
  const outputs = panel.getByTestId('job-panel-outputs');
  const card = outputs.getByTestId('artifact-card').filter({ hasText: file });
  await expect(card).toBeVisible({ timeout: 60_000 });
  const downloaded = page.waitForEvent('download', { timeout: 60_000 });
  await card.getByRole('button', { name: 'Download artifact' }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe(file);
  expect(readFileSync(await download.path(), 'utf8')).toBe(content);
});
