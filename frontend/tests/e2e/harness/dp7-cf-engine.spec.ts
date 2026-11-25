/**
 * DP7-MVP-1's engine conformance rows on the Compose stack (`run.sh durable`, the `harness-engine` project): the lane's
 * release engine (E2E_ENGINE: opencode by default, Claude Code with E2E_ENGINE=claude_code) on the scripted model,
 * the scripted SaaS behind the worker's tool gateway. Nothing is mocked.
 *
 *   CF-07-PH  a tool call round trip through `ph`, the sandbox's PipesHub CLI: the engine's shell finds the write
 *             with `ph tools search` and calls it with `ph tools call`; the gateway asks the person, runs it once
 *             allowed, and the result lands in the file the command named, which the person downloads
 *   CF-08-DENY the permission bridge's deny: the job's write asks, the person denies it with a reason; nothing is
 *             sent, the reason reaches the engine's next model call, and the engine carries on to its answer
 *             (the approve half is DE-2, CJ-E2 and QA-J2)
 *   J-03-XLSX  the xlsx skill on a real engine: the engine loads the skill, writes a real .xlsx with the image's
 *             openpyxl into the outputs folder, and the person downloads a workbook holding the rows
 */
import { randomBytes } from 'crypto';
import * as fs from 'fs';
import { inflateRawSync } from 'zlib';
import { test, expect } from '../fixtures/api-context.fixture';
import { WRITE_TOOL } from '../fixtures/classic-jobs-scenarios';
import { engineAgentKey, engineLane, handedOffJob, jobTimeline, releaseUntil } from './support/classic-jobs';
import { allowOnceButton, cancelLeftRuns, noActiveRun, script, ScriptedModel, ScriptedSaas, sendTurn } from './support/lane';

test.describe.configure({ mode: 'serial' });

const tag = () => randomBytes(3).toString('hex');

let model: ScriptedModel;
let saas: ScriptedSaas;

test.beforeAll(async () => {
  const l = engineLane();
  model = await ScriptedModel.open(l);
  saas = await ScriptedSaas.open(l);
});

test.afterAll(async () => {
  await model.close();
  await saas.close();
});

test.beforeEach(async () => {
  await model.reset();
  await saas.reset();
});

test.afterEach(async ({ apiContext }) => {
  await model.release();
  await cancelLeftRuns(apiContext);
});

test('CF-07-PH: a tool call through ph from the engine’s shell asks, runs once allowed, and its result is the file it named', async ({
  page,
  apiContext,
}) => {
  test.setTimeout(600_000);
  const agent = engineAgentKey();
  const t = tag();
  const title = `CF07 ${t}`;
  const file = `ticket-${t}.json`;
  const answer = `Ticket ${title} is filed; its receipt is ${file}.`;
  // A server's tools are built at its first use (ADR-OWD-07 D-LAZY-4), so the call names what `ph tools search` found.
  const found = `ph --json tools search ticket | python3 -c 'import json, sys; print(next(t["name"] for t in json.load(sys.stdin)["data"]["tools"] if t["name"].endswith("${WRITE_TOOL}")))'`;
  const call = `name=$(${found}) && ph --json tools call "$name" --json '${JSON.stringify({ title, body: 'From CF-07 through ph.' })}'`;
  const conversationId = await sendTurn(
    page,
    agent,
    script(`File the ticket ${title} with ph.`, `shell mkdir -p out && ${call} > out/${file} 2>&1; true`, `say ${answer}`),
  );
  const { jobRunId } = await handedOffJob(apiContext, agent, conversationId);
  const float = page.getByTestId('job-ask-float');
  await expect(float).toBeVisible({ timeout: 240_000 });
  expect(await saas.effects(), 'nothing runs while the ask waits').toEqual([]);
  await allowOnceButton(float, WRITE_TOOL).click();
  await expect.poll(async () => (await saas.effects()).map((e) => e.tool), { timeout: 120_000 }).toEqual([WRITE_TOOL]);
  await releaseUntil(model, async () => (await page.getByText(answer).count()) > 0, 'the job answers');
  await noActiveRun(apiContext, agent, conversationId);
  expect((await saas.effects()).length, 'the call ran once').toBe(1);
  const { approvals } = await jobTimeline(apiContext, agent, conversationId, jobRunId);
  expect(approvals.map((a) => a.status)).toEqual(['approved']);

  const download = page.getByRole('button', { name: 'Download artifact' });
  await expect(download.first()).toBeVisible({ timeout: 60_000 });
  const [saved] = await Promise.all([page.waitForEvent('download', { timeout: 60_000 }), download.last().click()]);
  const text = fs.readFileSync(await saved.path(), 'utf8');
  expect(saved.suggestedFilename()).toBe(file);
  const receipt = JSON.parse(text) as { ok: boolean; command: string; data: { tool: string; text: string } };
  expect(receipt).toMatchObject({ ok: true, command: 'tools.call' });
  expect(receipt.data.tool).toMatch(new RegExp(`_${WRITE_TOOL}$`));
  expect(receipt.data.text).toContain(`created ${(await saas.effects())[0]!.ticket}`);
});

test('CF-08-DENY: a denied write is never sent; the engine reads the reason and carries on to its answer', async ({
  page,
  apiContext,
}) => {
  test.setTimeout(600_000);
  const agent = engineAgentKey();
  const t = tag();
  const reason = `Not this quarter ${t}`;
  await model.watchFor([reason]);
  const answer = `Understood, no ticket for ${t}.`;
  const conversationId = await sendTurn(
    page,
    agent,
    script(
      `File the Q3 ticket ${t}.`,
      // The SaaS's tools are built at their server's first use (ADR-OWD-07 D-LAZY-4): the engine finds the write first.
      `tool search_tools ${JSON.stringify({ query: WRITE_TOOL })}`,
      `tool ${WRITE_TOOL} ${JSON.stringify({ title: `CF08 ${t}`, body: 'From CF-08.' })}`,
      `say ${answer}`,
    ),
  );
  const { jobRunId } = await handedOffJob(apiContext, agent, conversationId);
  const float = page.getByTestId('job-ask-float');
  await expect(float).toBeVisible({ timeout: 240_000 });
  await float.getByRole('button', { name: 'Details' }).click();
  const details = page.getByTestId('job-ask-details');
  await details.getByRole('button', { name: /^Deny / }).first().click();
  await details.getByTestId('deny-reason-input').fill(reason);
  await details.getByTestId('deny-confirm').click();

  await releaseUntil(model, async () => (await page.getByText(answer).count()) > 0, 'the engine answers after the deny');
  await expect(page.getByText(answer).last()).toBeVisible({ timeout: 60_000 });
  await noActiveRun(apiContext, agent, conversationId);
  expect(await saas.effects(), 'the denied write was never sent').toEqual([]);
  const { approvals, status } = await jobTimeline(apiContext, agent, conversationId, jobRunId);
  expect(status).toBe('completed');
  expect(approvals.map((a) => a.status)).toEqual(['denied']);
  const sightings = await model.sightings();
  expect(sightings.length, 'the reason reached the engine’s next model call').toBeGreaterThan(0);
});

/** One member of a zip file (the .xlsx container), by its name: stored or deflated, as openpyxl writes them. */
function zipMember(zip: Buffer, name: string): string | null {
  let at = 0;
  while (at + 30 <= zip.length && zip.readUInt32LE(at) === 0x04034b50) {
    const method = zip.readUInt16LE(at + 8);
    const size = zip.readUInt32LE(at + 18);
    const nameLength = zip.readUInt16LE(at + 26);
    const extra = zip.readUInt16LE(at + 28);
    const member = zip.subarray(at + 30, at + 30 + nameLength).toString('utf8');
    const start = at + 30 + nameLength + extra;
    if (member === name) {
      const data = zip.subarray(start, start + size);
      return (method === 8 ? inflateRawSync(data) : data).toString('utf8');
    }
    at = start + size;
  }
  return null;
}

test('J-03-XLSX: the engine loads the xlsx skill and the person downloads the workbook it wrote', async ({ page, apiContext }) => {
  test.setTimeout(600_000);
  const agent = engineAgentKey();
  const t = tag();
  // A line of the xlsx skill's own instructions: seen in a model call, the skill was loaded, not just named.
  await model.watchFor(['The one rule that matters most: write real formulas']);
  const file = `p1-issues-${t}.xlsx`;
  const answer = `The open P1 issues ${t} are in ${file}.`;
  const make = [
    'import openpyxl',
    'wb = openpyxl.Workbook()',
    'ws = wb.active',
    "ws.append(['Issue', 'Owner'])",
    `ws.append(['SEC-${t}', 'alice'])`,
    `wb.save('out/${file}')`,
  ].join('; ');
  const conversationId = await sendTurn(
    page,
    agent,
    script(`Make an XLSX of the open P1 issues ${t} with owners.`, 'skill xlsx', `shell mkdir -p out && python3 -c "${make}"`, `say ${answer}`),
  );
  const { jobRunId } = await handedOffJob(apiContext, agent, conversationId);
  await expect(page.getByText(answer).last()).toBeVisible({ timeout: 300_000 });
  await noActiveRun(apiContext, agent, conversationId);
  const job = (await jobTimeline(apiContext, agent, conversationId, jobRunId)) as unknown as {
    engine?: { frames?: Array<{ kind: string; text: string; data?: { skills?: string[] } }> };
  };
  const frames = job.engine?.frames ?? [];
  const steps = frames.map((f) => `${f.kind}:${f.text}`);
  // Each engine names its skill tool its own way (Claude Code's `Skill`, opencode's `skill`); one without a skill
  // tool (Codex) reads the skill's SKILL.md with its shell, a step the timeline names as the skill it read.
  const loaded = frames.findIndex(
    (f) =>
      (f.kind === 'tool_result' && /^skill$/i.test(f.text)) ||
      (f.kind === 'command' && (f.data?.skills ?? []).includes('xlsx')),
  );
  expect(loaded, `the engine loaded a skill before it wrote the file: ${JSON.stringify(steps)}`).toBeGreaterThan(-1);
  // Codex runs each command as `bash -lc '<command>'`: the step's text holds the command, wrapped or not.
  const wrote = steps.findIndex((step) => step.startsWith('command:') && step.includes('mkdir -p out'));
  expect(wrote, `the engine wrote the file after loading the skill: ${JSON.stringify(steps)}`).toBeGreaterThan(loaded);
  expect((await model.sightings()).length, 'the skill’s instructions reached the engine’s model').toBeGreaterThan(0);
  const download = page.getByRole('button', { name: 'Download artifact' });
  await expect(download).toHaveCount(1, { timeout: 60_000 });
  const [saved] = await Promise.all([page.waitForEvent('download', { timeout: 60_000 }), download.click()]);
  expect(saved.suggestedFilename()).toBe(file);
  const workbook = fs.readFileSync(await saved.path());
  expect(workbook.subarray(0, 2).toString('latin1'), 'a zip container').toBe('PK');
  const strings = zipMember(workbook, 'xl/sharedStrings.xml') ?? zipMember(workbook, 'xl/worksheets/sheet1.xml') ?? '';
  expect(strings).toContain(`SEC-${t}`);
  expect(strings).toContain('alice');
});
