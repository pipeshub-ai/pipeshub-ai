/**
 * Raw URLs read as short names in chat, in the question and in the answer,
 * for the assistant and for an agent: a GitHub pull request shows as `#4242`,
 * a Jira issue as its key. The full address stays in `href` and on hover.
 *
 * Nothing is mocked: the model is asked to repeat the URLs back, so the
 * answer's links come from real streamed markdown.
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { hasAnsweringModels, NO_MODEL_REASON } from '../helpers/ai-models.helper';

const PR = 'https://github.com/pipeshub-ai/pipeshub-ai/pull/4242';
const ISSUE = 'https://github.com/pipeshub-ai/pipeshub-ai/issues/4243';
const JIRA = 'https://example.atlassian.net/browse/DOCS-17';
const DOC = 'https://docs.pipeshub.com/connectors/overview';
const QUESTION = `Repeat these four links back to me as bare URLs, one per line, and nothing else: ${PR} ${ISSUE} ${JIRA} ${DOC}`;

async function askAndCheck(page: Page, shot: string) {
  const input = page.locator('textarea').last();
  await input.fill(QUESTION);
  await input.press('Enter');

  const question = page.getByTestId('user-query-heading').last();
  for (const [name, href] of [['#4242', PR], ['#4243', ISSUE], ['DOCS-17', JIRA]]) {
    const link = question.getByRole('link', { name, exact: true });
    await expect(link).toHaveAttribute('href', href);
    await expect(link).toHaveAttribute('title', href);
  }
  await expect(question).not.toContainText(PR);

  // One link in the question, one in the answer.
  await expect(page.getByRole('link', { name: '#4242', exact: true }), 'the answer should show the PR as #4242').toHaveCount(2, {
    timeout: 180_000,
  });
  await expect(page.getByRole('link', { name: 'DOCS-17', exact: true })).toHaveCount(2);
  await expect(page.getByText(PR, { exact: true })).toHaveCount(0);

  await page.getByRole('link', { name: '#4242', exact: true }).last().hover();
  await test.info().attach(shot, { body: await page.screenshot(), contentType: 'image/png' });
}

test.describe('Link labels in chat', () => {
  let agentKey: string | undefined;

  test.beforeEach(async ({ apiContext }) => {
    test.skip(!(await hasAnsweringModels(apiContext)), NO_MODEL_REASON);
  });

  test.afterEach(async ({ apiContext }) => {
    if (!agentKey) return;
    const res = await apiContext.delete(`/api/v1/agents/${agentKey}`);
    agentKey = undefined;
    expect(res.ok() || res.status() === 404, `deleting the test agent failed: ${res.status()}`).toBe(true);
  });

  test('the assistant chat shows pasted and answered URLs by short name', async ({ page }) => {
    test.setTimeout(240_000);
    await page.goto('/chat/');
    await askAndCheck(page, 'assistant-chat-links');
  });

  test('an agent chat shows pasted and answered URLs by short name', async ({ page }) => {
    test.setTimeout(300_000);
    await page.goto('/agents/new/');
    const create = page.getByRole('dialog', { name: 'Create agent' });
    await create.getByPlaceholder('e.g. Support bot').fill(`E2E Links ${Date.now().toString(36)}`);
    await create.getByRole('button', { name: 'Create agent' }).click();
    await expect(page).toHaveURL(/\/agents\/edit\/\?agentKey=/, { timeout: 30_000 });
    agentKey = new URL(page.url()).searchParams.get('agentKey') ?? undefined;
    expect(agentKey, 'the builder URL should carry the new agent key').toBeTruthy();

    await page.goto(`/chat/?agentId=${agentKey}`);
    await expect(page.getByRole('button', { name: 'Agent options' })).toBeVisible({ timeout: 30_000 });
    await askAndCheck(page, 'agent-chat-links');
  });
});
