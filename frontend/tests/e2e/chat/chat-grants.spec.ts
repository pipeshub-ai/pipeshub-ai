/**
 * chat-grants.spec.ts — BGUI-7 (design: the Context column's "Permissions in this chat"; ADR-GRANT-01 D8): the chat's
 * grants in its top bar in a real browser.
 *
 * A saved harness chat whose owner allowed two things "for this chat". The top-bar button shows only because the chat
 * holds them; its list names each in words with when it was allowed; Remove takes one back (`DELETE …/grants/{id}`),
 * the toast says the next call asks again, and removing the last leaves the open list saying nothing is allowed. A
 * chat with none, and a chat the person does not own, show no button. Every API is served here (page.route), sign-in
 * included: no stack, no model.
 *
 * With UI_CASE_SHOTS=<dir>, each state saves its pictures there: <state>-<desktop|phone>-<light|dark>.png.
 */
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';

const SESSION_ID = 'conv-bgui7';
const ASK = 'Post the Q3 numbers to #sales and check onyx.app for the pricing page';
const SHOTS = process.env.UI_CASE_SHOTS ?? '';
const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'quick', modelFriendlyName: 'GPT-4o mini' };
const SLACK = `cg_${'a'.repeat(40)}`;
const WEB = `cg_${'b'.repeat(40)}`;

const GRANTS = [
  { grantId: SLACK, toolId: 'slack@i/send_message', tool: { app: 'slack', name: 'send_message' }, destination: '#sales', sensitivity: 'private', grantedBy: 'user-bgui7', grantedByName: 'Ada Lovelace', grantedAt: '2026-11-08T10:00:00Z' },
  { grantId: WEB, toolId: 'web@i/fetch_url', tool: { app: 'web', name: 'fetch_url' }, destination: 'onyx.app', sensitivity: 'public', grantedBy: 'user-bgui7', grantedByName: 'Ada Lovelace', grantedAt: '2026-11-09T15:30:00Z' },
];

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

function fakeToken(): string {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${part({ alg: 'none', typ: 'JWT' })}.${part({ userId: 'user-bgui7', orgId: 'org-bgui7', email: 'bgui7@example.com', exp: 4_102_444_800 })}.sig`;
}

function conversation(owner: boolean) {
  const now = new Date().toISOString();
  const message = (id: string, messageType: string, content: string, extra: Record<string, unknown> = {}) => ({
    _id: id,
    messageType,
    content,
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    referenceData: [],
    modelInfo: MODEL_INFO,
    createdAt: now,
    updatedAt: now,
    feedback: [],
    ...extra,
  });
  const answer = 'I posted the numbers to #sales and read the pricing page on onyx.app.';
  return {
    _id: SESSION_ID,
    id: SESSION_ID,
    title: ASK,
    status: 'complete',
    durable: true,
    createdAt: now,
    updatedAt: now,
    access: owner ? { isOwner: true, accessLevel: 'OWNER' } : { isOwner: false, accessLevel: 'READ' },
    activeRunId: null,
    messages: [message('msg-u1', 'user_query', ASK), message('msg-b1', 'bot_response', answer, { parts: [{ type: 'text', content: answer, runId: 'run-bgui7' }], runId: 'run-bgui7', status: 'complete' })],
  };
}

async function mockBackend(page: Page, options: { owner?: boolean; grants?: typeof GRANTS } = {}): Promise<{ held: typeof GRANTS; reads: number; revoked: string[] }> {
  const owner = options.owner ?? true;
  const state = { held: [...(options.grants ?? GRANTS)], reads: 0, revoked: [] as string[] };
  const token = fakeToken();
  await page.addInitScript(([access]) => {
    window.localStorage.setItem('jwt_access_token', access);
    window.localStorage.setItem('jwt_refresh_token', access);
  }, [token]);
  await page.route('**/api/**', (route) => json(route, {}));
  await page.route('**/socket.io/**', (route) => route.abort());
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    json(route, { featureFlags: {}, agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true } }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    json(route, { status: 'success', message: 'Success', models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }] }),
  );
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route('**/api/v1/users/user-bgui7', (route) =>
    json(route, { _id: 'user-bgui7', fullName: 'Ada Lovelace', firstName: 'Ada', lastName: 'Lovelace', email: 'bgui7@example.com', hasLoggedIn: true, role: 'admin' }),
  );
  await page.route('**/api/v1/conversations*', (route) =>
    json(route, { conversations: [], source: 'owned', pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false } }),
  );
  const saved = conversation(owner);
  await page.route((url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${SESSION_ID}`, (route) => json(route, { conversation: saved, filters: {}, meta: {} }));
  // As Node serves them: both routes are the owner's alone, 404 for anyone else (ADR-GRANT-01 D8).
  await page.route((url) => url.pathname === `/api/v1/conversations/${SESSION_ID}/grants`, (route) => {
    state.reads += 1;
    return owner ? json(route, { grants: state.held }) : json(route, { error: { message: 'Not found' } }, 404);
  });
  await page.route((url) => url.pathname.startsWith(`/api/v1/conversations/${SESSION_ID}/grants/`), (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
    const gone = state.held.find((g) => g.grantId === id);
    if (route.request().method() !== 'DELETE' || !gone) return json(route, { error: { message: 'Not found' } }, 404);
    state.held = state.held.filter((g) => g.grantId !== id);
    state.revoked.push(id);
    return json(route, { revoked: gone, runs: 0 });
  });
  return state;
}

const AXE_SOURCE = readFileSync(createRequire(__filename).resolve('axe-core/axe.min.js'), 'utf8');

async function axeViolations(page: Page, include: string[]): Promise<unknown[]> {
  if (!(await page.evaluate(() => 'axe' in window))) await page.evaluate(AXE_SOURCE);
  return page.evaluate(async (selectors) => {
    type Result = { violations: Array<{ id: string; help: string; nodes: Array<{ target: string[] }> }> };
    const axe = (window as unknown as { axe: { run: (ctx: unknown, opts: unknown) => Promise<Result> } }).axe;
    const result = await axe.run({ include: selectors.map((s) => [s]) }, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }, resultTypes: ['violations'] });
    return result.violations.map((v) => ({ id: v.id, help: v.help, targets: v.nodes.slice(0, 5).map((n) => n.target.join(' ')) }));
  }, include);
}

async function shoot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  if (!existsSync(SHOTS)) mkdirSync(SHOTS, { recursive: true });
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity));
  await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}

const LOOKS = [
  { name: 'desktop-light', viewport: { width: 1440, height: 900 }, dark: false },
  { name: 'desktop-dark', viewport: { width: 1440, height: 900 }, dark: true },
  { name: 'phone-light', viewport: { width: 390, height: 844 }, dark: false },
  { name: 'phone-dark', viewport: { width: 390, height: 844 }, dark: true },
] as const;

async function look(page: Page, l: (typeof LOOKS)[number]): Promise<void> {
  await page.setViewportSize(l.viewport);
  await page.emulateMedia({ colorScheme: l.dark ? 'dark' : 'light' });
  await page.addInitScript((d) => localStorage.setItem('pipeshub-theme-preference', d ? 'dark' : 'light'), l.dark);
}

test.describe('Chat — Permissions in this chat (BGUI-7)', () => {
  for (const l of LOOKS) {
    test(`the top bar lists this chat's grants; Remove takes one back and the next call asks again (${l.name})`, async ({ page }) => {
      const phone = l.viewport.width < 500;
      const state = await mockBackend(page);
      await look(page, l);
      await page.goto(`/chat/?conversationId=${SESSION_ID}`);

      const button = page.getByTestId('chat-grants-button');
      await expect(button).toHaveAccessibleName('Permissions in this chat');
      if (phone) expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
      await button.click();

      const panel = page.getByTestId('chat-grants-panel');
      const rows = panel.getByTestId('chat-grant');
      await expect(rows).toHaveCount(2);
      await expect(rows.nth(0)).toContainText('Slack: Send message to #sales');
      await expect(rows.nth(0)).toContainText('Allowed on Nov 8');
      await expect(rows.nth(1)).toContainText('onyx.app');
      const remove = panel.getByRole('button', { name: 'Remove Slack: Send message to #sales' });
      if (phone) expect((await remove.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(await axeViolations(page, ['[data-testid="chat-grants-panel"]', '[data-testid="chat-grants-button"]'])).toEqual([]);
      await shoot(page, `01-list-${l.name}`);

      await remove.click();
      await expect(rows).toHaveCount(1);
      expect(state.revoked).toEqual([SLACK]);
      await expect(page.getByText('It asks you again the next time.')).toBeVisible();
      await shoot(page, `02-removed-${l.name}`);

      await panel.getByRole('button', { name: /^Remove / }).click();
      await expect(panel.getByTestId('chat-grants-empty')).toHaveText('Nothing is allowed for this chat yet. When you allow something for this chat, it shows here.');
      await shoot(page, `03-empty-${l.name}`);

      await page.keyboard.press('Escape');
      await expect(page.getByTestId('chat-grants-panel')).toHaveCount(0);
      await expect(page.getByTestId('chat-grants-button')).toHaveCount(0);
    });
  }

  test('a chat that holds no grant has no button', async ({ page }) => {
    const state = await mockBackend(page, { grants: [] });
    await page.goto(`/chat/?conversationId=${SESSION_ID}`);
    await expect(page.getByText(ASK).first()).toBeVisible();
    await expect.poll(() => state.reads).toBeGreaterThan(0);
    await expect(page.getByTestId('chat-grants-button')).toHaveCount(0);
  });

  test('a chat shared with me shows me no grants and never asks for them', async ({ page }) => {
    const state = await mockBackend(page, { owner: false });
    await page.goto(`/chat/?conversationId=${SESSION_ID}`);
    await expect(page.getByText(ASK).first()).toBeVisible();
    await expect(page.getByTestId('chat-grants-button')).toHaveCount(0);
    expect(state.reads).toBe(0);
  });
});
