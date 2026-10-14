/**
 * The first admin on a fresh install: sign up, go through onboarding, land in chat.
 *
 * Needs a stack with no organization yet, so it runs only in its own project
 * (E2E_FIRST_RUN=true, `--project=first-run`), before CI creates the org the
 * other tests sign in to. It signs up with TEST_USER_EMAIL / TEST_USER_PASSWORD,
 * so those tests then use the account made here.
 *
 * Nothing is mocked. The AI and embedding models are "OpenAI Compatible" models
 * pointed at the stand-in served by the integration stack's `web-fixtures`
 * service, and the real backend health-checks each one before saving it.
 */
import { request, type APIRequestContext, type Page } from '@playwright/test';
import { test, expect } from '../fixtures/base.fixture';
import { apiBaseURL } from '../fixtures/api-context.fixture';

const FIXTURE_SITE = (process.env.WEB_FIXTURES_CONNECTOR_URL ?? 'http://web-fixtures:8080').replace(/\/$/, '');
const STAND_IN_ENDPOINT = `${FIXTURE_SITE}/__fixtures__/openai/v1/`;
const MODELS_API = '/api/v1/configurationManager/ai-models';

const WORKSPACE_NAME = 'First Run Org';

function credentials(): { email: string; password: string } {
  const email = process.env.TEST_USER_EMAIL;
  const password = process.env.TEST_USER_PASSWORD;
  if (!email || !password) {
    throw new Error('TEST_USER_EMAIL and TEST_USER_PASSWORD must be set: the account is created with them.');
  }
  return { email, password };
}

async function signedInApi(page: Page): Promise<APIRequestContext> {
  const token = await page.evaluate(() => window.localStorage.getItem('jwt_access_token'));
  expect(token, 'no access token was stored after onboarding').toBeTruthy();
  return request.newContext({
    baseURL: apiBaseURL(),
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
}

/** Adds an OpenAI-compatible model through the open step's provider grid and returns its key. */
async function addStandInModel(
  page: Page,
  { friendlyName, modelId, endpointPlaceholder }: { friendlyName: string; modelId: string; endpointPlaceholder: string },
): Promise<string> {
  await page
    .getByTestId('ai-provider-openAICompatible')
    .getByRole('button', { name: /Configure/ })
    .click();

  await page.getByPlaceholder('e.g., My Custom Model').fill(friendlyName);
  await page.getByPlaceholder(endpointPlaceholder).fill(STAND_IN_ENDPOINT);
  await page.getByPlaceholder('Your API Key').fill('fixture-key');
  // The Model dropdown takes a typed id: Enter picks it ahead of any listed model.
  const modelField = page.getByTestId('ai-model-combobox').getByRole('combobox');
  await modelField.fill(modelId);
  await modelField.press('Enter');
  await expect(page.getByRole('button', { name: `Remove ${modelId}` })).toBeVisible();

  // Saving runs the backend's live health check against the stand-in.
  const saved = page.waitForResponse(
    (r) => r.request().method() === 'POST' && r.url().includes(`${MODELS_API}/providers`),
    { timeout: 90_000 },
  );
  await page.getByRole('button', { name: 'Add Model' }).click();
  const response = await saved;
  const body = (await response.json()) as { message?: string; details?: { modelKey?: string } };
  expect(response.status(), `saving ${friendlyName} failed: ${body.message ?? ''}`).toBe(200);
  const modelKey = body.details?.modelKey;
  expect(modelKey, `saving ${friendlyName} returned no model key`).toBeTruthy();
  return modelKey!;
}

test.describe('First run', () => {
  test('the first admin signs up, completes onboarding and lands in chat signed in', async ({ page }) => {
    test.setTimeout(300_000);
    const { email, password } = credentials();
    const suffix = Date.now().toString(36);

    const anon = await request.newContext({ baseURL: apiBaseURL() });
    const exists = (await (await anon.get('/api/v1/org/exists')).json()) as { exists?: boolean };
    await anon.dispose();
    expect(exists.exists, 'this test needs a fresh install with no organization').toBe(false);

    // ── Sign up ─────────────────────────────────────────────────────────────
    // A fresh install sends a visitor to sign-up, not to the login page.
    await page.goto('/');
    await page.waitForURL(/\/sign-up\/?(\?|$)/, { timeout: 30_000 });
    await expect(page.getByText('Get Started', { exact: true })).toBeVisible({ timeout: 30_000 });

    await page.locator('#first-name').fill('First');
    await page.locator('#last-name').fill('Admin');
    await page.locator('#registered-name').fill(WORKSPACE_NAME);
    await page.locator('#signup-email').fill(email);
    await page.locator('#signup-password').fill(password);
    await page.locator('#signup-confirm-password').fill(password);
    await page.getByRole('button', { name: 'Continue', exact: true }).click();

    const dialog = page.getByRole('alertdialog');
    await expect(dialog.getByText('How PipesHub assigns permissions', { exact: true })).toBeVisible();
    await expect(dialog.getByText(email, { exact: true })).toBeVisible();
    const created = page.waitForResponse(
      (r) => r.request().method() === 'POST' && /\/api\/v1\/org\/?$/.test(new URL(r.url()).pathname),
    );
    await dialog.getByRole('button', { name: 'Sign Up', exact: true }).click();
    const createdResponse = await created;
    expect(createdResponse.status(), `creating the organization failed: ${await createdResponse.text()}`).toBe(200);

    // ── Onboarding, step 1: the AI model ───────────────────────────────────
    await page.waitForURL(/\/onboarding\/?\?step=ai-model/, { timeout: 30_000 });
    await expect(page.getByText('Step 1/3: Configure AI Model*', { exact: true })).toBeVisible({ timeout: 30_000 });
    // The header names the account that was just created.
    await expect(page.getByText(email, { exact: true })).toBeVisible();

    const next = page.getByRole('button', { name: /^Next/ });
    await expect(next).toBeDisabled();

    const llmName = `First Run LLM ${suffix}`;
    const llmKey = await addStandInModel(page, {
      friendlyName: llmName,
      modelId: `e2e-first-run-llm-${suffix}`,
      endpointPlaceholder: 'e.g., https://api.together.xyz/v1/',
    });

    // With a model saved, onboarding asks which model indexes documents.
    await expect(page.getByText('How will your models be used?', { exact: true })).toBeVisible({ timeout: 20_000 });
    await page.getByRole('combobox').filter({ hasText: 'Select a model' }).click();
    const rolesSaved = page.waitForResponse(
      (r) => r.request().method() === 'PUT' && r.url().includes(`${MODELS_API}/roles`),
    );
    await page.getByRole('option', { name: 'Same as query model', exact: true }).click();
    expect((await rolesSaved).status(), 'saving the model roles failed').toBe(200);
    await expect(page.getByText('Not set — falls back to your query model.', { exact: true })).toBeVisible();

    await expect(next).toBeEnabled();
    await next.click();

    // ── Step 2: the embedding model ────────────────────────────────────────
    await page.waitForURL(/\/onboarding\/?\?step=embedding-model/, { timeout: 30_000 });
    await expect(page.getByText('Step 2/3: Configure Embedding Model*', { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(next).toBeDisabled();

    const embeddingName = `First Run Embedding ${suffix}`;
    const embeddingKey = await addStandInModel(page, {
      friendlyName: embeddingName,
      modelId: `e2e-first-run-embedding-${suffix}`,
      endpointPlaceholder: 'e.g., https://api.openai.com/v1',
    });

    await expect(next).toBeEnabled({ timeout: 20_000 });
    await next.click();

    // ── Step 3: storage, the last step ─────────────────────────────────────
    await page.waitForURL(/\/onboarding\/?\?step=storage/, { timeout: 30_000 });
    await expect(page.getByText('Step 3/3: Configure Storage', { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('select')).toHaveValue('local');

    const storageSaved = page.waitForResponse(
      (r) => r.request().method() === 'POST' && r.url().includes('/api/v1/configurationManager/storageConfig'),
    );
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    const storageResponse = await storageSaved;
    expect(storageResponse.status(), `saving storage failed: ${await storageResponse.text()}`).toBe(200);

    await page.getByRole('button', { name: /^Enter Pipeshub/ }).click();

    // ── Home, signed in ────────────────────────────────────────────────────
    await page.waitForURL('**/chat/**', { timeout: 30_000 });
    await expect(page).toHaveURL(/\/chat\//);

    // What the steps saved is what the server now holds, read with the stored session.
    const api = await signedInApi(page);
    try {
      const status = await api.get('/api/v1/org/onboarding-status');
      expect(status.ok(), `reading onboarding status failed: ${status.status()}`).toBe(true);
      expect(((await status.json()) as { status?: string }).status).toBe('configured');

      const org = await api.get('/api/v1/org/');
      expect(org.ok(), `reading the organization failed: ${org.status()}`).toBe(true);
      expect(((await org.json()) as { registeredName?: string }).registeredName).toBe(WORKSPACE_NAME);

      const storage = await api.get('/api/v1/configurationManager/storageConfig');
      expect(storage.ok(), `reading the storage config failed: ${storage.status()}`).toBe(true);
      expect(((await storage.json()) as { storageType?: string }).storageType).toBe('local');

      for (const [type, key] of [['llm', llmKey], ['embedding', embeddingKey]] as const) {
        const listed = await api.get(`${MODELS_API}/${type}`);
        expect(listed.ok(), `listing ${type} models failed: ${listed.status()}`).toBe(true);
        const models = ((await listed.json()) as { models?: { modelKey: string }[] }).models ?? [];
        expect(models.map((m) => m.modelKey), `the ${type} model saved in onboarding is not configured`).toContain(key);
      }
    } finally {
      await api.dispose();
    }
  });
});
