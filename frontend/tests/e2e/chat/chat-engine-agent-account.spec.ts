/**
 * chat-engine-agent-account.spec.ts — AG1 PW-1 and PW-3 on the e2e lane (deployment/e2e/run.sh).
 *
 * Real Node, query service and Neo4j, the real KV store and credential vault.
 * An engine agent's account choice is stored and read back through the agents
 * module; its chat turn is admitted by Node and, since CTL-6, runs on the loop:
 * the account is chosen when the turn hands off to its engine child, and the
 * browser never names one (no request body the page sends carries
 * `modelAccountId`). The harness lane runs no engine, so PW-3 asserts
 * the admission (a run on the conversation), not an engine run; the engine run
 * itself is chat-durable-engine-e2e.spec.ts (nightly).
 *
 * PW-3 needs durable admission (the lane's harness relay and worker
 * on, and the org's ENABLE_AGENT_HARNESS, which this spec turns on for its own
 * duration and then restores).
 */
import type { APIRequestContext, Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { setLabsFlag } from '../helpers/labs-flag.helper';
import { conversationPath, recordPostBodies } from '../harness/support/lane';

test.describe.configure({ mode: 'serial' });

const HARNESS_FLAG = 'ENABLE_AGENT_HARNESS';
/** What the person reads when an agent's model account is not there (chatStream.admission.accountMissing). */
const ACCOUNT_MISSING_TEXT = "This agent's model account isn't set up here.";

async function harnessReady(api: APIRequestContext): Promise<string> {
  const effective = (await (
    await api.get('/api/v1/configurationManager/platform/feature-flags/effective')
  ).json()) as { agentHarness?: { useHarness?: boolean; reason?: string } | null };
  return effective.agentHarness?.useHarness ? 'enabled' : (effective.agentHarness?.reason ?? 'unknown');
}

async function engineProfile(api: APIRequestContext, agentKey: string): Promise<unknown> {
  const res = await api.get(`/api/v1/agents/${agentKey}`);
  expect(res.ok()).toBe(true);
  return ((await res.json()) as { agent?: { engineProfile?: unknown } }).agent?.engineProfile;
}

/**
 * The choice sits in the agent node's coding-engine section, under "Engine settings" (it left the prompts dialog).
 * WP-80: the editor opens readable on the agent card, so the button is in view without panning the canvas.
 */
async function openAccountChoice(page: Page, agentKey: string) {
  await page.goto(`/agents/edit/?agentKey=${agentKey}`);
  const settings = page.getByRole('button', { name: 'Engine settings' });
  await expect(settings).toBeInViewport({ timeout: 30_000 });
  await settings.click();
  await expect(settings).toHaveAttribute('aria-expanded', 'true');
  const group = page.getByRole('radiogroup', { name: 'AI account' });
  await expect(group).toBeVisible({ timeout: 30_000 });
  return { group };
}

/** Types one chat turn to the agent; answers what the page sent (whichever route admits the turn). */
/**
 * The run the turn was admitted as: running, or (CTL-6: a loop turn on the lane's scripted model ends quickly) the
 * run its answer was saved from.
 */
async function admittedRunId(api: APIRequestContext, agentKey: string, conversationId: string): Promise<string | null> {
  let runId: string | null = null;
  await expect
    .poll(async () => {
      const res = await api.get(conversationPath(agentKey, conversationId));
      const body = (await res.json()) as {
        conversation?: { activeRunId?: string | null; messages?: Array<{ runId?: string }> };
      };
      runId = body.conversation?.activeRunId ?? body.conversation?.messages?.find((m) => m.runId)?.runId ?? null;
      return runId;
    }, { message: 'the turn was admitted as a run', timeout: 60_000 })
    .not.toBeNull();
  return runId;
}

async function typeTurn(page: Page, agentKey: string): Promise<string[]> {
  const sent = recordPostBodies(page);
  await page.goto(`/chat/?agentId=${agentKey}`);
  const input = page.locator('textarea').last();
  await input.waitFor({ timeout: 30_000 });
  await input.fill('What changed this quarter?');
  await input.press('Enter');
  return sent;
}

test.describe('Engine agents choose their AI account (AG1)', () => {
  let agentKey: string | undefined;
  let harnessWasOn = false;

  test.beforeAll(async ({ apiContext }) => {
    const res = await apiContext.post('/api/v1/agents/create', {
      data: {
        name: `E2E engine agent ${Date.now().toString(36)}`,
        description: 'An agent that runs on an engine.',
        systemPrompt: 'You are an analyst.',
        engineProfile: {},
      },
    });
    expect(res.ok(), `creating the engine agent failed: ${res.status()} ${await res.text()}`).toBe(true);
    const agent = ((await res.json()) as { agent?: { _key?: string; engineProfile?: unknown } }).agent;
    agentKey = agent?._key;
    expect(agentKey).toBeTruthy();
    expect(agent?.engineProfile).toEqual({});
    harnessWasOn = await setLabsFlag(apiContext, HARNESS_FLAG, true);
  });

  test.afterAll(async ({ apiContext }) => {
    await setLabsFlag(apiContext, HARNESS_FLAG, harnessWasOn);
    if (agentKey) await apiContext.delete(`/api/v1/agents/${agentKey}`);
  });

  test('PW-1: the editor stores the account choice and reads it back after a reload', async ({ page, apiContext }) => {
    const { group } = await openAccountChoice(page, agentKey!);
    await expect(group.getByRole('radio', { name: 'Any account the organization allows' })).toBeChecked();
    await expect(group.getByRole('radio', { name: 'Personal key' })).toBeDisabled();
    await group.getByRole('radio', { name: 'Organization key' }).click();
    const saved = page.waitForResponse(
      (r) => r.request().method() === 'PUT' && r.url().includes(`/api/v1/agents/${agentKey}`),
    );
    // OWD-14: "Publish" replaced "Save changes"; it asks to confirm the version it publishes.
    await page.getByRole('button', { name: /Publish$/ }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Publish', exact: true }).click();
    const response = await saved;
    expect(response.ok(), `saving failed: ${response.status()}`).toBe(true);
    expect((response.request().postDataJSON() as { engineProfile?: unknown }).engineProfile).toEqual({
      accountKinds: ['org_api_key'],
    });

    expect(await engineProfile(apiContext, agentKey!)).toEqual({ accountKinds: ['org_api_key'] });
    const reopened = await openAccountChoice(page, agentKey!);
    await expect(reopened.group.getByRole('radio', { name: 'Organization key' })).toBeChecked();
  });

  test('PW-3: an agent that admits the org key is admitted on it; the browser names no account', async ({
    page,
    apiContext,
  }) => {
    expect(await harnessReady(apiContext), 'durable admission is on for the org').toBe('enabled');
    const put = await apiContext.put(`/api/v1/agents/${agentKey}`, { data: { engineProfile: {} } });
    expect(put.ok(), await put.text()).toBe(true);
    const sent = await typeTurn(page, agentKey!);
    await expect(page).toHaveURL(/conversationId=/, { timeout: 60_000 });
    const conversationId = new URL(page.url()).searchParams.get('conversationId')!;
    expect(await admittedRunId(apiContext, agentKey!, conversationId), 'admitted as a run').toBeTruthy();
    await expect(page.getByText(ACCOUNT_MISSING_TEXT)).toHaveCount(0);
    expect(sent.join('\n')).not.toContain('modelAccountId');
  });
});
