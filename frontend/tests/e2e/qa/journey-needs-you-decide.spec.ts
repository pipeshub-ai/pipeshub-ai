/**
 * QA-CLASSIC journey J4 (was QA-AUTO's Inbox journey): Needs you → open the ask → decide → done, in the classic UI.
 * A chat's write waits on the person while they are elsewhere; the sidebar's Needs you count and the chat's mark say
 * so; the Needs you page lists the ask; Review in chat lands on its approval card; Allow runs the write once; the
 * answer lands; and neither Needs you nor the chat's mark shows it waiting any more. Scripted model and SaaS only.
 *
 * CJ-5 covers a job's ask on these surfaces; this is a plain chat turn's ask, from leaving the chat to coming back.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { allowOnceButton, cancelLeftRuns, lane, noActiveRun, script, ScriptedModel, ScriptedSaas, type Lane } from '../harness/support/lane';
import { answerText, chatMark, discover, needsYouBadge, needsYouEntry, needsYouRowOf, sendInNewChat, showRecents } from './classic';

test.describe.configure({ mode: 'serial' });

const TOOL = 'create_ticket';
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
test.afterEach(async ({ apiContext }) => {
  await cancelLeftRuns(apiContext);
});

test('QA-J4: an approval waits in Needs you; Review in chat opens its card; Allow finishes the run and clears it', async ({ page, apiContext }) => {
  const title = `QAJ4 ${randomBytes(4).toString('hex')}`;
  const done = `Ticket ${title} is filed.`;
  const conversationId = await sendInNewChat(
    page,
    script(`File a ticket titled ${title}.`, discover(TOOL), `tool ${TOOL} ${JSON.stringify({ title, body: 'Filed by QA-J4.' })}`, `say ${done}`),
    l.toolAgentKey,
  );
  await expect(allowOnceButton(page, TOOL)).toBeVisible({ timeout: 90_000 });

  // Elsewhere (a new chat): the sidebar counts it; the agent's chat list marks its chat.
  await page.goto('/chat/');
  await expect(needsYouBadge(page)).toBeVisible({ timeout: 30_000 });
  await page.goto(`/chat/?agentId=${l.toolAgentKey}`);
  await showRecents(page, conversationId);
  await expect(chatMark(page, conversationId)).toHaveAttribute('data-dot', 'needsYou', { timeout: 30_000 });

  // Needs you lists the ask in words; Review in chat goes to the chat, at the ask.
  await expect(needsYouBadge(page)).toBeVisible();
  await needsYouEntry(page).click();
  await expect(page).toHaveURL(/\/jobs\/\?view=needs-you/);
  const row = needsYouRowOf(page, conversationId);
  await expect(row).toHaveCount(1, { timeout: 30_000 });
  // The row's title words the ask as the chat's card does (QA-CLASSIC-FU-2): the action and its app, never the tool's
  // id. Its meta line repeats the chat's title, which here is the scripted message that names the tool.
  await expect(row.getByText('Create ticket in E2Esaas?', { exact: true })).toBeVisible();
  await expect(row).not.toContainText(`E2Esaas · ${TOOL}`);
  await expect(row).not.toContainText('Approve:');
  await row.getByRole('link', { name: 'Review in chat' }).click();
  await expect(page).toHaveURL(new RegExp(`conversationId=${conversationId}`));
  const allow = allowOnceButton(page, TOOL);
  await expect(allow).toBeVisible({ timeout: 30_000 });
  await expect(allow).toBeInViewport();
  expect(await saas.effects(), 'nothing ran from Needs you').toEqual([]);

  await allow.click();
  await expect(answerText(page, done)).toBeVisible({ timeout: 90_000 });
  await noActiveRun(apiContext, l.toolAgentKey, conversationId);
  expect((await saas.effects()).map((e) => e.tool)).toEqual([TOOL]);

  // Back in Needs you: the ask no longer waits, and the chat's mark is not "Needs you".
  await needsYouEntry(page).click();
  await expect(page).toHaveURL(/\/jobs\/\?view=needs-you/);
  await expect(page.getByRole('tab', { name: /^Needs you/ })).toHaveAttribute('aria-selected', 'true', { timeout: 30_000 });
  await expect(needsYouRowOf(page, conversationId)).toHaveCount(0, { timeout: 30_000 });
  await page.goto(`/chat/?agentId=${l.toolAgentKey}`);
  await showRecents(page, conversationId);
  await expect(chatMark(page, conversationId).and(page.locator('[data-dot="needsYou"]'))).toHaveCount(0);
});
