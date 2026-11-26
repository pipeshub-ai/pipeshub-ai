/**
 * MCPNOTE-1: the answer names each MCP server a run went without and how to fix it, in the real page on a stack. The
 * turn is replayed into the page as Node sends it (the saved conversation's notice parts, as
 * `projection/transcript.ts` writes them); nothing reaches a model or an MCP server.
 *
 *   MCPNOTE-1a  one server that wants a sign-in: plain words, and a "Sign in to …" button (the MCP OAuth popup)
 *   MCPNOTE-1b  several servers and reasons: one sentence per reason, each server once, the links that fix them
 *
 * Shots with UI_CASE_SHOTS: `mcpnote-1a`, `mcpnote-1b` (support/case-shots.ts).
 */
import { randomBytes } from 'crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { buildAguiConversation } from '../chat/agui-sse-builder';
import { json, MODEL_INFO } from '../chat/durable-conversation.mock';
import { chatInput } from './support/lane';
import { followSystemTheme, iconFonts, shoot } from './support/case-shots';
import { expectCardsFitAtEveryWidth } from './support/card-fit';
import { expectCardsReadableInBothThemes } from './support/card-contrast';

const frame = (type: string, fields: Record<string, unknown> = {}): string =>
  `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;

type Part = Record<string, unknown>;

async function replayTurn(page: Page, question: string, answer: string, parts: Part[]): Promise<void> {
  const id = `conv-mcpnote-${randomBytes(3).toString('hex')}`;
  const base = buildAguiConversation({ conversationId: id, userMessageId: `u-${id}`, question, modelInfo: MODEL_INFO, answer });
  const stored = { ...base, messages: base.messages.map((m) => (m['messageType'] === 'bot_response' ? { ...m, parts } : m)) };
  const frames =
    frame('CUSTOM', { name: 'conversation_created', value: { conversationId: id } }) +
    frame('TEXT_MESSAGE_START') + frame('TEXT_MESSAGE_CONTENT', { delta: answer }) + frame('TEXT_MESSAGE_END') +
    frame('RUN_FINISHED', { result: { conversation: stored, meta: { requestId: id, duration: 1 } } });
  const turn = /\/api\/v1\/(agents\/[^/]+\/)?conversations\/stream$/;
  await page.route((url) => turn.test(url.pathname), (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: frames })
      : route.fallback(),
  );
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

const unused = (item: string, reason: string, instance?: string): Part => ({
  type: 'notice',
  code: 'mcp_unavailable',
  item,
  reason,
  ...(instance ? { instance } : {}),
  runId: 'run-mcpnote',
});
const finalText = (content: string): Part => ({ type: 'text', content, isFinal: true });
const notes = (page: Page) => page.getByRole('note');

test.beforeEach(async ({ page }) => {
  await followSystemTheme(page);
  await iconFonts(page);
});

test.describe('MCPNOTE-1 the answer names the MCP servers it went without (replay)', () => {
  test('MCPNOTE-1a: one server that wants a sign-in, with its sign-in one click away', async ({ page }) => {
    const answer = 'Here is what e2esaas had on onboarding.';
    await replayTurn(page, `mcpnote-1a ${randomBytes(2).toString('hex')}`, answer, [
      unused('Atlassian Rovo', 'sign_in', 'mcp-rovo'),
      finalText(answer),
    ]);
    const note = notes(page).last();
    await expect(note).toContainText("Atlassian Rovo wasn't used for this answer: sign in to it first.", { timeout: 30_000 });
    await expect(note.getByRole('button', { name: 'Sign in to Atlassian Rovo' })).toBeVisible();
    await expectCardsFitAtEveryWidth(page, () => notes(page), 'MCPNOTE-1a notice');
    await expectCardsReadableInBothThemes(page, () => notes(page), 'MCPNOTE-1a notice');
    await shoot(page, 'mcpnote-1a', () => note);
  });

  test('MCPNOTE-1b: several servers, one sentence per reason, each with what fixes it', async ({ page }) => {
    const answer = 'Done, without three of your MCP servers.';
    await replayTurn(page, `mcpnote-1b ${randomBytes(2).toString('hex')}`, answer, [
      unused('Atlassian Rovo', 'sign_in', 'mcp-rovo'),
      unused('Linear', 'sign_in', 'mcp-linear'),
      unused('Wiki', 'unreachable'),
      unused('Figma', 'not_approved'),
      finalText(answer),
    ]);
    await expect(notes(page).first()).toContainText(
      "Atlassian Rovo and Linear weren't used for this answer: sign in to them first.",
      { timeout: 30_000 },
    );
    await expect(page.getByRole('button', { name: 'Sign in to Linear' })).toBeVisible();
    await expect(notes(page).nth(1)).toContainText("Wiki wasn't used for this answer: it didn't respond.");
    await expect(page.getByRole('link', { name: 'Open MCP servers' })).toHaveAttribute('href', /\/workspace\/mcp-servers\/personal/);
    // The seeded user is the org's admin: the tools are theirs to approve.
    await expect(notes(page).nth(2)).toContainText("Figma wasn't used for this answer: an admin hasn't approved any of its tools yet.");
    await expect(page.getByRole('link', { name: 'Approve its tools' })).toHaveAttribute('href', /\/workspace\/mcp-servers\/team/);
    await expectCardsFitAtEveryWidth(page, () => notes(page), 'MCPNOTE-1b notices');
    await expectCardsReadableInBothThemes(page, () => notes(page), 'MCPNOTE-1b notices');
    await shoot(page, 'mcpnote-1b', () => notes(page).first().locator('..'));
  });
});
