/**
 * Setting up a reranker through the AI Models screen, end to end.
 *
 * Nothing is mocked: an admin adds an "OpenAI Compatible" reranker in the
 * Reranking tab and the real backend health-checks it (the passage that answers
 * a question must rank first) before saving. The endpoint is the integration
 * stack's `web-fixtures` stand-in reranker, so no paid provider is needed.
 */
import { test, expect } from '../fixtures/api-context.fixture';

const FIXTURE_SITE = (process.env.WEB_FIXTURES_CONNECTOR_URL ?? 'http://web-fixtures:8080').replace(/\/$/, '');
const STAND_IN_ENDPOINT = `${FIXTURE_SITE}/__fixtures__/openai/v1/`;
const CONFIG_API = '/api/v1/configurationManager';
const MODELS_API = `${CONFIG_API}/ai-models`;

test.describe('Set up a reranker', () => {
  let modelKey: string | undefined;

  test.afterEach(async ({ apiContext }) => {
    if (modelKey) {
      const removed = await apiContext.delete(`${MODELS_API}/providers/reranker/${modelKey}`);
      expect(removed.ok(), `deleting the test reranker failed: ${removed.status()} ${await removed.text()}`).toBe(true);
      modelKey = undefined;
    }
  });

  test('an admin adds an OpenAI-compatible reranker and it is listed', async ({ page }) => {
    test.setTimeout(120_000);
    const friendlyName = `E2E Reranker ${Date.now().toString(36)}`;

    await page.goto('/workspace/ai-models/');
    await page.getByRole('button', { name: 'For Reranking' }).click();
    await page
      .getByTestId('ai-provider-openAICompatible')
      .getByRole('button', { name: /Configure/ })
      .click();

    await page.getByPlaceholder('e.g., http://vllm:8000/v1').fill(STAND_IN_ENDPOINT);
    await page.getByPlaceholder('Your API Key').fill('fixture-key');
    await page.getByPlaceholder('e.g., BAAI/bge-reranker-v2-m3').fill('e2e-stand-in-reranker');
    await page.getByPlaceholder('e.g., My Custom Model').fill(friendlyName);

    const saved = page.waitForResponse(
      (r) => r.request().method() === 'POST' && r.url().includes(`${MODELS_API}/providers`),
      { timeout: 60_000 },
    );
    await page.getByRole('button', { name: 'Add Model' }).click();
    const response = await saved;
    const body = (await response.json()) as { message?: string; details?: { modelKey?: string } };
    expect(response.status(), `saving the reranker failed: ${body.message ?? ''}`).toBe(200);
    expect(JSON.parse(response.request().postData() ?? '{}')).toMatchObject({ modelType: 'reranker' });
    modelKey = body.details?.modelKey;
    expect(modelKey, 'the save response carried no model key').toBeTruthy();

    await page.getByRole('radio', { name: /^Configured Models/ }).click();
    await expect(page.getByText(friendlyName)).toBeVisible({ timeout: 20_000 });
  });

  test('the Reranking tab says when reranking is off, and Labs can turn it on', async ({ page, apiContext }) => {
    const res = await apiContext.get(`${CONFIG_API}/platform/feature-flags/effective`);
    expect(res.ok(), `reading feature flags failed: ${res.status()}`).toBe(true);
    const flags = ((await res.json()) as { flags?: Record<string, boolean> }).flags ?? {};

    await page.goto('/workspace/ai-models/');
    await page.getByRole('button', { name: 'For Reranking' }).click();
    const hint = page.getByTestId('reranker-flag-hint');
    if (flags.ENABLE_RERANKER) {
      await expect(hint).toHaveCount(0);
    } else {
      await expect(hint).toBeVisible();
      await hint.getByRole('link', { name: 'Labs' }).click();
      await expect(page).toHaveURL(/\/workspace\/labs/);
      await expect(page.getByText('Enable Reranker', { exact: true })).toBeVisible();
    }
  });
});
