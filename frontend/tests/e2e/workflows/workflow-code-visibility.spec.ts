import { test, expect } from '../fixtures/base.fixture';
import type { Page } from '@playwright/test';

// ---------------------------------------------------------------------------
// Regression coverage for the "can't see already-generated workflow code"
// bug: a workflow whose code generated successfully must show the Graph/Code
// studio, not the "Generate Code" prompt, and a version-store outage must
// surface as a retry-able error banner rather than silently looking the same
// as "no code was ever generated." All backend calls are mocked via
// `page.route` (same approach as `chat/chat-basic.spec.ts`) so this suite
// does not depend on a live Python/graph-DB backend.
// ---------------------------------------------------------------------------

const WORKFLOW_ID = 'wf-e2e-1';

const CODE_WORKFLOW = {
  workflowId: WORKFLOW_ID,
  orgId: 'org-1',
  kind: 'code',
  name: 'Daily digest',
  description: 'Posts open Jira issues to #general every morning',
  currentVersionId: 'ver-1',
  triggers: [],
  status: 'active',
  requiredScopes: [],
  executionKind: 'code',
  createdByUserId: 'u-1',
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
};

const AGENT_TASK_WORKFLOW = {
  ...CODE_WORKFLOW,
  kind: 'agent_task',
  currentVersionId: null,
  executionKind: 'agent_task',
};

const IR_WITH_A_NODE = {
  nodes: [
    { node_id: 'n1', kind: 'workflow', label: 'daily_digest', source_start: 1, source_end: 4, children: [], metadata: {} },
  ],
  edges: [],
  entry_node_id: 'n1',
};

const VERSION_1 = {
  versionId: 'ver-1',
  workflowId: WORKFLOW_ID,
  versionNumber: 1,
  sdkVersion: '0.1.0',
  contentHash: 'abc1234def',
  hasBundleRef: true,
  createdAt: '2026-01-01T00:00:00Z',
  createdByUserId: 'u-1',
  ir: IR_WITH_A_NODE,
};

const SOURCE_V1 = [
  '@sdk.workflow(name="daily_digest")',
  'async def daily_digest(ctx, trigger_payload):',
  '    issues = await ctx.tool("jira__search_issues")',
  '    return issues',
].join('\n');

function jsonRoute(page: Page, pattern: string, body: unknown, status = 200) {
  return page.route(pattern, (route) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  );
}

async function mockCommonWorkflowApis(page: Page, workflow: typeof CODE_WORKFLOW) {
  await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}`, workflow);
  await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/triggers`, { triggers: [] });
  await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/runs*`, {
    runs: [], total: 0, limit: 20, offset: 0, hasMore: false,
  });
}

test.describe('Workflow detail — code visualization for already-generated code', () => {
  test('a code workflow with an existing version shows the studio, not "Generate Code"', async ({ page }) => {
    await mockCommonWorkflowApis(page, CODE_WORKFLOW);
    await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/versions`, { versions: [VERSION_1] });
    await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/versions/ver-1/source`, {
      versionId: 'ver-1', workflowId: WORKFLOW_ID, source: SOURCE_V1,
    });

    await page.goto(`/workflows?workflowId=${WORKFLOW_ID}`);

    await expect(page.getByRole('button', { name: /Graph/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: /Code/ })).toBeVisible();
    await expect(page.getByText(/optionally generate code/i)).not.toBeVisible();
  });

  test('an agent_task workflow whose version exists (pin failed) still shows the studio', async ({ page }) => {
    // BUG-2 regression: previously a version whose pin failed was deleted,
    // so `versions` came back empty and the workflow was stuck on
    // "Generate Code" despite the code having been generated successfully.
    await mockCommonWorkflowApis(page, AGENT_TASK_WORKFLOW);
    await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/versions`, { versions: [VERSION_1] });
    await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/versions/ver-1/source`, {
      versionId: 'ver-1', workflowId: WORKFLOW_ID, source: SOURCE_V1,
    });

    await page.goto(`/workflows?workflowId=${WORKFLOW_ID}`);

    await expect(page.getByRole('button', { name: /Graph/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/optionally generate code/i)).not.toBeVisible();
  });

  test('editing and committing code surfaces the new version in the selector', async ({ page }) => {
    await mockCommonWorkflowApis(page, CODE_WORKFLOW);
    await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/versions`, { versions: [VERSION_1] });
    await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/versions/ver-1/source`, {
      versionId: 'ver-1', workflowId: WORKFLOW_ID, source: SOURCE_V1,
    });

    const editedSource = SOURCE_V1.replace('return issues', 'await ctx.tool("slack__post_message")\n    return issues');
    await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/edit`, {
      source: editedSource, ir: IR_WITH_A_NODE, previousSource: SOURCE_V1, baseVersionId: 'ver-1',
    });
    await jsonRoute(page, `**/api/v1/workflows/${WORKFLOW_ID}/versions/commit`, {
      ...VERSION_1, versionId: 'ver-2', versionNumber: 2, contentHash: 'def5678',
    });

    await page.goto(`/workflows?workflowId=${WORKFLOW_ID}`);
    await expect(page.getByRole('button', { name: /Graph/ })).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: /Edit/ }).click();
    await page.getByRole('textbox').first().fill('Also post to Slack');
    await page.getByRole('button', { name: /Generate Changes/ }).click();
    await expect(page.getByRole('button', { name: /Apply Changes/ })).toBeVisible({ timeout: 10_000 });
    await page.getByRole('button', { name: /Apply Changes/ }).click();

    // The version selector now offers the newly committed version.
    await expect(page.getByText(/v2/)).toBeVisible({ timeout: 10_000 });
  });
});

test.describe('Workflow detail — version store unavailable', () => {
  test('shows a retry-able error banner instead of "Generate Code" on a 503', async ({ page }) => {
    await mockCommonWorkflowApis(page, AGENT_TASK_WORKFLOW);
    let attempt = 0;
    await page.route(`**/api/v1/workflows/${WORKFLOW_ID}/versions`, (route) => {
      attempt += 1;
      if (attempt === 1) {
        return route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ message: `Version store unavailable for workflow ${WORKFLOW_ID}` }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ versions: [] }),
      });
    });

    await page.goto(`/workflows?workflowId=${WORKFLOW_ID}`);

    await expect(page.getByText(/Could not load this workflow's code/i)).toBeVisible({ timeout: 15_000 });
    const retryButton = page.getByRole('button', { name: /Retry/ });
    await expect(retryButton).toBeVisible();
    await expect(page.getByText(/optionally generate code/i)).not.toBeVisible();

    await retryButton.click();

    await expect(page.getByText(/optionally generate code/i)).toBeVisible({ timeout: 10_000 });
    expect(attempt).toBeGreaterThanOrEqual(2);
  });
});
