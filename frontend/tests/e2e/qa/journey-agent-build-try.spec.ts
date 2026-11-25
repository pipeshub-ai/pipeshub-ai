/**
 * QA-CLASSIC journey J5 (was QA-AUTO's new-interface agent journey): agent build → try → act, in the classic agent
 * builder. An agent is created and given instructions in the builder and published; its owner gives it the scripted
 * SaaS's write tool (the one the seeded tool agent holds); Open in chat starts the new agent's chat; its first write
 * asks before it runs; Allow runs it once; the answer lands, and a reload keeps the answer in the agent's chat.
 * Scripted model and SaaS only.
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, cancelLeftRuns, lane, script, ScriptedModel, ScriptedSaas, trackTurn, type Lane } from '../harness/support/lane';
import { answerText, composer, discover } from './classic';

test.describe.configure({ mode: 'serial' });

const TOOL = 'create_ticket';
let l: Lane;
let model: ScriptedModel;
let saas: ScriptedSaas;
const made: string[] = [];

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
  for (const key of made.splice(0)) await apiContext.delete(`/api/v1/agents/${key}`);
});

/** Publishes the builder's changes as `version`; the buttons' names carry their icon's ligature first. */
async function publish(page: Page, version: number): Promise<void> {
  await page.getByRole('button', { name: /Publish$/ }).click();
  const confirm = page.getByRole('alertdialog');
  await expect(confirm.getByRole('heading', { name: `Publish version ${version}?` })).toBeVisible();
  await confirm.getByRole('button', { name: 'Publish', exact: true }).click();
  const published = page.getByRole('dialog', { name: `Version ${version} published` });
  await expect(published).toBeVisible({ timeout: 30_000 });
  await published.getByRole('button', { name: 'Continue editing' }).click();
  await expect(page.getByTestId('agent-version-status')).toContainText(`Live: version ${version}`);
}

type Server = { instanceId: string; name: string; tools: Array<{ name: string; fullName: string }> };

test('QA-J5: built in the builder, opened in chat, and its first write asks before it runs', async ({ page, apiContext }) => {
  await model.reset();
  await saas.reset();
  const name = `Ticket desk ${randomBytes(3).toString('hex')}`;

  await page.goto('/agents/new/');
  const create = page.getByRole('dialog', { name: 'Create agent' });
  await create.getByPlaceholder('e.g. Support bot').fill(name);
  await create.getByRole('button', { name: /Create agent$/ }).click();
  await expect(page).toHaveURL(/\/agents\/edit\/\?agentKey=/, { timeout: 30_000 });
  const agentKey = new URL(page.url()).searchParams.get('agentKey') as string;
  expect(agentKey, 'the builder names the new agent').toBeTruthy();
  made.push(agentKey);

  await page.getByRole('button', { name: 'Edit prompts' }).click();
  const prompts = page.getByRole('dialog', { name: 'Configure agent prompts' });
  await prompts.getByRole('textbox').first().fill('You file tickets for the team, and check with the person before you act.');
  await prompts.getByRole('button', { name: 'Save' }).click();
  await publish(page, 2);

  // Its owner gives it the scripted SaaS's write tool, the one the seeded tool agent holds; the builder shows the
  // change as unpublished, and publishing it makes it what chats run.
  const seeded = (await (await apiContext.get(`/api/v1/agents/${l.toolAgentKey}`)).json()) as { agent: { mcpServers: Server[] } };
  const mcpServers = seeded.agent.mcpServers.map((s) => ({ instanceId: s.instanceId, name: s.name, tools: s.tools.map((t) => ({ name: t.name, fullName: t.fullName })) }));
  const granted = await apiContext.put(`/api/v1/agents/${agentKey}`, { data: { mcpServers } });
  expect(granted.ok(), `granting the tool: ${granted.status()} ${await granted.text()}`).toBe(true);
  const after = (await (await apiContext.get(`/api/v1/agents/${agentKey}/versions`)).json()) as { unpublishedChanges: boolean };
  await page.reload();
  if (after.unpublishedChanges) {
    await expect(page.getByTestId('agent-version-status')).toContainText('Unpublished changes', { timeout: 30_000 });
    await publish(page, 3);
  }

  // Try it: the new agent's chat; its write asks first and runs once on Allow.
  await page.getByRole('button', { name: /Open in chat$/ }).click();
  await expect(page).toHaveURL(new RegExp(`/chat/\\?agentId=${agentKey}`), { timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Agent options' })).toContainText(name, { timeout: 30_000 });
  const title = `QAJ5 ${randomBytes(3).toString('hex')}`;
  const done = `Ticket ${title} is filed.`;
  await composer(page).fill(script(`File a ticket titled ${title}.`, discover(TOOL), `tool ${TOOL} ${JSON.stringify({ title, body: 'Filed by QA-J5.' })}`, `say ${done}`));
  await composer(page).press('Enter');
  await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
  const conversationId = new URL(page.url()).searchParams.get('conversationId') as string;
  trackTurn(agentKey, conversationId);
  const allow = allowOnceButton(page, TOOL);
  await expect(allow).toBeVisible({ timeout: 90_000 });
  expect(await saas.effects(), 'nothing runs before Allow').toEqual([]);
  await allow.click();
  await expect(answerText(page, done)).toBeVisible({ timeout: 90_000 });
  expect((await saas.effects()).map((e) => e.tool)).toEqual([TOOL]);

  await page.reload();
  await expect(answerText(page, done)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Agent options' })).toContainText(name);
  expect(await saas.effects(), 'a reload runs nothing again').toHaveLength(1);
});
