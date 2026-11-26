/**
 * chat-tool-names.spec.ts — TOOLNAMES (tool names in activity read as ids such as
 * dynamic__web_search and image_generator__generate_image). A saved multi-tool answer, as the server stores it
 * (built-ins without a display name, an MCP batch with the worker's "Server · id"), is opened in the real chat
 * page: the steps header, the timeline rows and the activity drawer name every tool in words. Session routes are
 * mocked with page.route(); the `setup` project signs in against the e2e stack, like chat-activity-panel.spec.ts.
 *
 * TOOLNAMES_SHOTS=<dir> also writes the evidence shots there (evidence/toolnames/).
 */
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { buildAguiConversation } from './agui-sse-builder';

const SESSION_ID = 'conv-toolnames';
const RUN_ID = 'run-toolnames';
const QUESTION = 'Find the 2026 travel policy, draw a cover image and file the follow-up tickets';
const ANSWER = 'Done: the policy is summarised above, the cover image is attached and three tickets are filed in E2Esaas.';
const MODEL_INFO = { modelKey: 'gpt-4o-mini', modelName: 'GPT-4o mini', chatMode: 'quick', modelFriendlyName: 'GPT-4o mini' };
const SHOTS = process.env.TOOLNAMES_SHOTS;
const TAG = process.env.TOOLNAMES_TAG ?? 'after';
const RAW = /__|@|web_search|generate_image|create_ticket|fetch_url|run_code/;

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

const CALLS: Array<{ toolName: string; displayName?: string; argsSummary?: string }> = [
  { toolName: 'dynamic__web_search', argsSummary: 'travel policy 2026' },
  { toolName: 'dynamic__fetch_url', argsSummary: 'https://example.com/travel-policy' },
  { toolName: 'image_generator__generate_image', argsSummary: 'A calm airport lounge at dawn' },
  { toolName: 'pipeshub@sandbox/run_code', argsSummary: 'Running python code' },
  { toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket' },
  { toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket' },
  { toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket' },
];

function parts(): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  CALLS.forEach((call, i) => {
    if (i < 5) out.push({ type: 'reasoning', content: `Step ${i + 1}.`, runId: RUN_ID });
    out.push({ type: 'tool_call', toolCallId: `call-${i}`, status: 'completed', runId: RUN_ID, args: '{}', ...call });
  });
  out.push({ type: 'text', content: ANSWER, isFinalAnswer: true, runId: RUN_ID });
  return out;
}

function activityView() {
  return {
    runId: RUN_ID,
    status: 'completed',
    commands: [],
    omittedCommands: 0,
    tools: CALLS.map((call, i) => ({ toolCallId: `call-${i}`, name: call.toolName, ...(call.displayName ? { displayName: call.displayName } : {}), status: 'completed' })),
    tasks: [],
    files: [],
    followups: [],
  };
}

async function mockBackend(page: Page): Promise<void> {
  await page.route('**/api/v1/configurationManager/platform/feature-flags/effective', (route) =>
    json(route, { featureFlags: {}, agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true } }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/available/llm', (route) =>
    json(route, {
      status: 'success',
      message: 'Success',
      models: [{ ...MODEL_INFO, modelType: 'chat', provider: 'openAI', isMultimodal: false, isReasoning: false, isDefault: true }],
    }),
  );
  await page.route('**/api/v1/users/by-ids', (route) => json(route, []));
  await page.route('**/api/v1/conversations*', (route) =>
    route.request().method() === 'GET'
      ? json(route, {
          conversations: [],
          source: 'owned',
          pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0, hasNextPage: false, hasPrevPage: false },
        })
      : route.fallback(),
  );
  const base = `**/api/v1/conversations/${SESSION_ID}`;
  await page.route(`${base}/runs/${RUN_ID}/timeline`, (route) =>
    json(route, { runId: RUN_ID, parentRunId: null, status: 'completed', parts: [], engine: null, approvals: [], children: [], merge: null, budget: null, followups: [] }),
  );
  await page.route((url) => url.pathname === `/api/v1/conversations/${SESSION_ID}/runs/${RUN_ID}/followups`, (route) => json(route, { followups: [] }));
  await page.route(`${base}/runs/${RUN_ID}/activity`, (route) =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: `event: snapshot\ndata: ${JSON.stringify(activityView())}\n\nevent: end\ndata: {"status":"completed"}\n\n`,
    }),
  );
  await page.route(`${base}/runs/${RUN_ID}/interactions/authority`, (route) => json(route, { approve: true, deny: true }));
  await page.route(`${base}/workspace/versions`, (route) => json(route, { workspaceId: `ws:${SESSION_ID}`, head: 0, versions: [] }));
  await page.route(
    (url) => url.pathname.replace(/\/$/, '') === `/api/v1/conversations/${SESSION_ID}`,
    (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const conversation = buildAguiConversation({
        conversationId: SESSION_ID,
        userMessageId: 'msg-user-toolnames',
        botMessageId: 'msg-bot-toolnames',
        question: QUESTION,
        answer: ANSWER,
        modelInfo: MODEL_INFO,
      });
      Object.assign(conversation.messages[1] as Record<string, unknown>, { parts: parts(), runId: RUN_ID });
      return json(route, { conversation: { ...conversation, id: SESSION_ID, access: { isOwner: true, accessLevel: 'OWNER' }, activeRunId: null }, filters: {}, meta: {} });
    },
  );
}

async function shot(page: Page, name: string) {
  await page.waitForTimeout(400);
  if (!SHOTS) return;
  await page.addStyleTag({ content: 'nextjs-portal { display: none !important; }' });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

test.describe('Chat — tool names in words (TOOLNAMES, needs the e2e stack)', () => {
  test.use({ viewport: { width: 1280, height: 1000 } });

  test('the steps header, the rows and the activity drawer never show a tool id', async ({ page }) => {
    await mockBackend(page);
    await page.goto(`/chat/?conversationId=${SESSION_ID}`);
    const header = page.getByTestId('activity-summary').first();
    await expect(header).toBeVisible({ timeout: 20_000 });
    // A saved answer's steps start folded; open them.
    if ((await header.getAttribute('aria-expanded')) === 'false') await header.click();
    await expect(header).toHaveAttribute('aria-expanded', 'true');
    // Shots first, so the same spec records the code before TOOLNAMES too (TOOLNAMES_TAG=before).
    await shot(page, `${TAG}-01-timeline`);
    await page.getByRole('button', { name: 'View activity', exact: true }).click();
    const panel = page.getByRole('complementary', { name: 'Activity' });
    // Repeated calls of one tool are one row that opens onto each call: four single rows and the three tickets.
    const group = panel.getByTestId('activity-step-group');
    await expect(panel.getByTestId('activity-tool')).toHaveCount(CALLS.length - 3);
    await expect(group.getByTestId('activity-step-group-name')).toHaveText('E2Esaas · Create ticket (3 times)');
    await group.getByRole('button', { name: /Create ticket \(3 times\)/ }).click();
    await expect(panel.getByTestId('activity-tool')).toHaveCount(CALLS.length);
    await shot(page, `${TAG}-02-activity-drawer`);

    await expect(header).toContainText('Searched the web');
    await expect(header).not.toContainText(RAW);
    for (const shown of ['Searched the web', 'Read a page', 'Image Generator', 'Generated an image', 'Ran code', 'E2Esaas']) {
      await expect(page.getByText(shown).first()).toBeVisible();
    }
    const names = panel.getByTestId('activity-tool-name');
    await expect(group.getByTestId('activity-tool-name')).toHaveText(Array(3).fill('E2Esaas · Create ticket'));
    expect(await names.allTextContents()).not.toContainEqual(expect.stringMatching(RAW));
  });
});
