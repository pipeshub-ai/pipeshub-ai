/**
 * ACTRESULTS on the harness lane's real stack: a scripted durable turn looks up its tools, reads three notes from
 * the scripted MCP server and creates a ticket after the person allows it. The Activity panel shows each step's
 * outcome, groups the three reads, folds the lookup into one quiet line, opens a step to the result the chat
 * shows, keeps the arguments behind Details, and shows the same after a reload (durable replay).
 *
 * When /work/pw/actresults-shots exists in the lane's run directory, it also saves the panel's pictures there
 * (desktop and phone, light and dark) for the work package's evidence.
 */
import { existsSync, writeFileSync } from 'fs';
import { randomBytes } from 'crypto';
import type { APIRequestContext, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, lane, noActiveRun, script, ScriptedModel, ScriptedSaas, sendTurn, type Lane } from './support/lane';

test.describe.configure({ mode: 'serial' });

const READ = 'lookup_note';
const WRITE = 'create_ticket';
const SHOTS = '/work/pw/actresults-shots';
const ANSWER = 'The finance review is due on Friday; the ticket is filed.';

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
test.beforeEach(async () => {
  await model.reset();
  await saas.reset();
});

const turn = (tag: string) =>
  script(
    `actresults ${tag}`,
    `tool search_tools ${JSON.stringify({ query: READ, make_visible: true })}`,
    `tool ${READ} ${JSON.stringify({ topic: 'release' })}`,
    `tool ${READ} ${JSON.stringify({ topic: 'canary' })}`,
    `tool ${READ} ${JSON.stringify({ topic: 'rollback' })}`,
    `tool search_tools ${JSON.stringify({ query: WRITE, make_visible: true })}`,
    `tool ${WRITE} ${JSON.stringify({ title: `Release ${tag}`, body: 'Notes: https://docs.acme.dev/release-process' })}`,
    `say ${ANSWER}`,
  );

const panel = (page: Page) => page.getByTestId('activity-panel');

async function openPanel(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'View activity' }).last().click();
  await expect(panel(page)).toBeVisible({ timeout: 30_000 });
  await expect(panel(page).getByTestId('activity-steps')).toBeVisible({ timeout: 30_000 });
}

async function shoot(page: Page, name: string): Promise<void> {
  if (!existsSync(SHOTS)) return;
  // A picture taken before the icon font loads shows the icons' names instead.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => document.fonts.check('24px "Material Symbols Outlined"'), null, { timeout: 30_000 }).catch(() => undefined);
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

/** The steps as a person reads them, before anything is opened. */
async function expectSteps(page: Page): Promise<void> {
  const steps = panel(page).getByTestId('activity-steps');
  // One quiet line for each lookup between real steps: before the reads and before the write.
  await expect(steps.getByTestId('activity-step-quiet')).toHaveText([/Looked up available tools/, /Looked up available tools/]);
  const group = steps.getByTestId('activity-step-group');
  await expect(group.getByTestId('activity-step-group-name')).toContainText('(3 times)');
  await expect(group).toContainText('3 finished');
  const ticket = steps.getByTestId('activity-tool').last();
  await expect(ticket).toContainText('Approved');
  await expect(steps).not.toContainText('search_tools');
  await expect(steps).not.toContainText(READ);
}

type McpGrant = { instanceId: string; name: string; tools: Array<{ name: string; fullName: string }> };

/** The seed grants the tool agent the SaaS's write tool only; the turn also reads, so the read tool is added. */
async function grantRead(api: APIRequestContext, agentKey: string): Promise<void> {
  const read = await api.get(`/api/v1/agents/${agentKey}`);
  expect(read.ok()).toBe(true);
  const servers = ((await read.json()) as { agent?: { mcpServers?: McpGrant[] } }).agent?.mcpServers ?? [];
  expect(servers.length, 'the seeded agent has the scripted SaaS').toBeGreaterThan(0);
  const [server] = servers;
  if (server!.tools.some((tool) => tool.name === READ)) return;
  // The update takes the grant's own fields only, as the seed writes it (`seed.py` grant_mcp).
  const tools = [...server!.tools, { name: READ, fullName: `mcp_${server!.name}_${READ}` }].map(({ name, fullName }) => ({ name, fullName }));
  const write = await api.put(`/api/v1/agents/${agentKey}`, {
    data: { mcpServers: [{ instanceId: server!.instanceId, name: server!.name, tools }] },
  });
  expect(write.ok(), `grant ${READ}: ${write.status()} ${await write.text()}`).toBe(true);
}

test('ACTRESULTS: the panel shows each step\'s outcome, its result one click away, live and after a reload', async ({ page, apiContext }) => {
  // The evidence run also takes nine pictures, each waiting for the icon font, on four page loads.
  if (existsSync(SHOTS)) test.setTimeout(300_000);
  await grantRead(apiContext, l.toolAgentKey);
  const tag = randomBytes(4).toString('hex');
  const conversationId = await sendTurn(page, l.toolAgentKey, turn(tag));
  await allowOnceButton(page, WRITE).waitFor({ timeout: 90_000 });

  // Live: the reads are in, the write waits for the person, and the panel says where to decide.
  await openPanel(page);
  const waiting = panel(page).getByTestId('activity-tool').last();
  await expect(waiting.getByTestId('activity-tool-reason')).toContainText('Decide on the approval card in the chat.', { timeout: 30_000 });
  await shoot(page, '01-live-waiting-desktop-light');

  await allowOnceButton(page, WRITE).click();
  await expect(page.getByText(ANSWER, { exact: true })).toBeVisible({ timeout: 90_000 });
  await noActiveRun(apiContext, l.toolAgentKey, conversationId);
  await expectSteps(page);
  await shoot(page, '02-done-desktop-light');

  // A read opens to the result the chat shows; its arguments stay behind Details.
  const group = panel(page).getByTestId('activity-step-group');
  await group.getByRole('button').first().click();
  const first = group.getByTestId('activity-tool').first();
  await expect(first.getByTestId('activity-tool-subject')).toContainText('release');
  // The snapshot carries no result: opening the step reads it once, from the owner-only step route.
  const read = page.waitForResponse((response) => /\/runs\/[^/]+\/steps\/[^/]+$/.test(new URL(response.url()).pathname));
  const opened = Date.now();
  await first.getByTestId('activity-tool-toggle').click();
  const response = await read;
  expect(response.status()).toBe(200);
  await expect(first.getByTestId('tool-result')).toContainText('Private note on release');
  const shown = Date.now() - opened;
  const timing = response.request().timing();
  if (existsSync(SHOTS)) {
    writeFileSync(
      `${SHOTS}/open-latency.json`,
      JSON.stringify({ clickToResultMs: shown, requestMs: Math.round(timing.responseEnd - timing.requestStart) }),
    );
  }
  await expect(first.getByTestId('tool-arguments')).toHaveCount(0);
  await first.getByRole('button', { name: 'Details' }).click();
  await expect(first.getByTestId('tool-arguments')).toContainText('release');
  await shoot(page, '03-result-open-desktop-light');

  // Durable replay: a reload rebuilds the same steps from the journal.
  await page.reload();
  await openPanel(page);
  await expectSteps(page);
  expect((await saas.effects()).map((e) => e.tool)).toEqual([WRITE]);

  if (!existsSync(SHOTS)) return;
  const url = page.url();
  for (const [name, viewport, dark] of [
    ['desktop-dark', { width: 1440, height: 960 }, true],
    ['phone-light', { width: 390, height: 844 }, false],
    ['phone-dark', { width: 390, height: 844 }, true],
  ] as const) {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
    await page.evaluate((d) => localStorage.setItem('pipeshub-theme-preference', d ? 'dark' : 'light'), dark);
    await page.goto(url);
    await openPanel(page);
    await shoot(page, `04-done-${name}`);
    const again = panel(page).getByTestId('activity-step-group');
    await again.getByRole('button').first().click();
    const read = again.getByTestId('activity-tool').first();
    await read.getByTestId('activity-tool-toggle').click();
    await expect(read.getByTestId('tool-result')).toContainText('Private note on release');
    await read.scrollIntoViewIfNeeded();
    await shoot(page, `05-result-open-${name}`);
  }
});
