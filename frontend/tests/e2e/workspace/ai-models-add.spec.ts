/**
 * Setting up an AI model through the AI Models screen, end to end.
 *
 * Nothing is mocked: an admin adds an "OpenAI Compatible" model in the UI and
 * the real backend health-checks it before saving. The endpoint is a stand-in
 * OpenAI-compatible model served by the integration stack's `web-fixtures`
 * service, so the test needs no paid provider account and can't fail because a
 * provider key ran out of credit. The saved model must then be offered in chat.
 */
import { test, expect } from '../fixtures/api-context.fixture';

const FIXTURE_SITE = (process.env.WEB_FIXTURES_CONNECTOR_URL ?? 'http://web-fixtures:8080').replace(/\/$/, '');
const STAND_IN_ENDPOINT = `${FIXTURE_SITE}/__fixtures__/openai/v1/`;
const MODELS_API = '/api/v1/configurationManager/ai-models';

type LlmModel = { modelKey: string; isDefault?: boolean };

async function listLlms(apiContext: import('@playwright/test').APIRequestContext): Promise<LlmModel[]> {
  const res = await apiContext.get(`${MODELS_API}/llm`);
  expect(res.ok(), `listing LLMs failed: ${res.status()} ${await res.text()}`).toBe(true);
  return ((await res.json()) as { models?: LlmModel[] }).models ?? [];
}

test.describe('Set up an AI model', () => {
  let modelKey: string | undefined;
  let priorDefault: string | undefined;

  test.beforeEach(async ({ apiContext }) => {
    priorDefault = (await listLlms(apiContext)).find((m) => m.isDefault)?.modelKey;
  });

  test.afterEach(async ({ apiContext }) => {
    if (modelKey) {
      const removed = await apiContext.delete(`${MODELS_API}/providers/llm/${modelKey}`);
      expect(removed.ok(), `deleting the test model failed: ${removed.status()} ${await removed.text()}`).toBe(true);
      modelKey = undefined;
    }
    // Adding a model can make it the default; give the default back so other
    // tests keep the model they were set up with.
    if (priorDefault && (await listLlms(apiContext)).find((m) => m.isDefault)?.modelKey !== priorDefault) {
      const restored = await apiContext.put(`${MODELS_API}/default/llm/${priorDefault}`);
      expect(restored.ok(), `restoring the default model failed: ${restored.status()} ${await restored.text()}`).toBe(true);
    }
  });

  test('an admin adds an OpenAI-compatible model and it is offered in chat', async ({ page }) => {
    test.setTimeout(180_000);
    const suffix = Date.now().toString(36);
    const friendlyName = `E2E Stand-in ${suffix}`;

    await page.goto('/workspace/ai-models/');
    await page
      .getByTestId('ai-provider-openAICompatible')
      .getByRole('button', { name: /Configure/ })
      .click();

    await page.getByPlaceholder('e.g., My Custom Model').fill(friendlyName);
    await page.getByPlaceholder('e.g., https://api.together.xyz/v1/').fill(STAND_IN_ENDPOINT);
    await page.getByPlaceholder('Your API Key').fill('fixture-key');
    await page.getByPlaceholder('e.g. deepseek-ai/DeepSeek-V3').fill(`e2e-stand-in-${suffix}`);

    // Saving runs the backend's live health check against the endpoint.
    const saved = page.waitForResponse(
      (r) => r.request().method() === 'POST' && r.url().includes(`${MODELS_API}/providers`),
      { timeout: 90_000 },
    );
    await page.getByRole('button', { name: 'Add Model' }).click();
    const response = await saved;
    const body = (await response.json()) as { message?: string; details?: { modelKey?: string } };
    expect(response.status(), `saving the model failed: ${body.message ?? ''}`).toBe(200);
    modelKey = body.details?.modelKey;
    expect(modelKey, 'the save response carried no model key').toBeTruthy();

    // It is listed among the configured models...
    // The switch reads "Configured Models <count>".
    await page.getByRole('radio', { name: /^Configured Models/ }).click();
    await expect(page.getByText(friendlyName)).toBeVisible({ timeout: 20_000 });

    // ...and offered in chat, from the real available-models API.
    await page.goto('/chat/');
    await page.locator('[data-testid="chat-model-selector"]:visible').click();
    await expect(page.getByTestId('chat-model-panel').getByText(friendlyName)).toBeVisible({ timeout: 20_000 });
  });
});
