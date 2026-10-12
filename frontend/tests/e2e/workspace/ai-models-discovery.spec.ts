/**
 * Multi-model add with a mocked discovery list and a partial batch failure.
 * The provider catalog, discover call, and batch stream are intercepted so the
 * test does not need a live model provider.
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';

type DiscoveryMode = 'list' | 'manual';

interface BatchBody {
  connectionId?: string;
  configuration: Record<string, unknown>;
  models: { model: string; isReasoning: boolean; isMultimodal: boolean; contextLength?: number | null }[];
}

function registry(mode: DiscoveryMode) {
  return {
    success: true,
    total: 1,
    providers: [
      {
        providerId: 'openAI',
        name: 'OpenAI',
        description: 'OpenAI',
        capabilities: ['text_generation'],
        iconPath: '/icons/ai-models/openai.svg',
        color: '#000',
        discovery: { mode, requiredFields: ['apiKey'] },
        fields: {
          text_generation: [
            { name: 'apiKey', displayName: 'API Key', fieldType: 'PASSWORD', required: true, placeholder: 'Discovery API key' },
            { name: 'model', displayName: 'Model', fieldType: 'TEXT', required: true, placeholder: 'Discovery model id' },
            { name: 'contextLength', displayName: 'Context Length', fieldType: 'NUMBER', required: false },
            { name: 'isReasoning', displayName: 'Reasoning', fieldType: 'BOOLEAN', required: false, defaultValue: false },
            { name: 'isMultimodal', displayName: 'Multimodal', fieldType: 'BOOLEAN', required: false, defaultValue: false },
          ],
        },
      },
    ],
  };
}

const DISCOVERED = {
  success: true,
  supported: true,
  models: [
    { id: 'gpt-4o', displayName: 'GPT-4o', capabilities: ['llm'], contextLength: 128000, isMultimodal: true },
    { id: 'gpt-4o-mini', displayName: 'GPT-4o mini', capabilities: ['llm'] },
    { id: 'o3', displayName: 'o3', capabilities: ['llm'], isReasoning: true },
  ],
  warnings: [],
};

function batchStream(models: string[], failModel: string | null): string {
  const lines: string[] = [];
  for (const id of models) {
    lines.push(`event: progress\ndata: ${JSON.stringify({ model: id, status: 'checking' })}\n`);
    lines.push(
      id === failModel
        ? `event: progress\ndata: ${JSON.stringify({ model: id, status: 'failed', message: 'rejected' })}\n`
        : `event: progress\ndata: ${JSON.stringify({ model: id, status: 'healthy', modelKey: id })}\n`,
    );
  }
  const saved = models.filter((id) => id !== failModel).length;
  lines.push(`event: done\ndata: ${JSON.stringify({ connectionId: 'conn-e2e', saved, aborted: false })}\n`);
  return `${lines.join('\n')}\n`;
}

async function mockAiModels(page: Page, mode: DiscoveryMode = 'list'): Promise<BatchBody[]> {
  const batchBodies: BatchBody[] = [];
  await page.route('**/api/v1/configurationManager/ai-models/registry**', (route) =>
    route.fulfill({ json: registry(mode) }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/discover', (route) =>
    route.fulfill({ json: DISCOVERED }),
  );
  await page.route('**/api/v1/configurationManager/ai-models/providers/batch', (route) => {
    const body = route.request().postDataJSON() as BatchBody;
    batchBodies.push(body);
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: batchStream(
        body.models.map((item) => item.model),
        batchBodies.length === 1 ? 'o3' : null,
      ),
    });
  });
  await page.route('**/api/v1/configurationManager/ai-models', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({
      json: { status: 'success', models: { llm: [], embedding: [] }, message: 'ok' },
    });
  });
  await page.route('**/api/v1/configurationManager/ai-models/roles', (route) =>
    route.fulfill({ json: { status: 'success', roles: {} } }),
  );
  return batchBodies;
}

async function openOpenAI(page: Page) {
  await page.getByTestId('ai-provider-openAI').getByRole('button', { name: /Configure/ }).click();
  await page.getByPlaceholder('Discovery API key').fill('sk-test');
}

async function pickThreeModels(page: Page) {
  await openOpenAI(page);
  await page.getByTestId('ai-fetch-models').click();
  for (const label of ['GPT-4o', 'GPT-4o mini', 'o3']) {
    await page.getByLabel(label, { exact: true }).click();
  }
}

test('an admin picks models, sets a flag per model, and retries the one that failed', async ({ page }) => {
  const batchBodies = await mockAiModels(page);
  await page.goto('/workspace/ai-models/');
  await pickThreeModels(page);

  await expect(page.getByTestId('ai-picked-models')).toBeVisible();
  await expect(page.getByPlaceholder('Discovery model id')).toHaveCount(0);
  await page.getByRole('switch', { name: 'Multimodal for gpt-4o-mini' }).click();
  await page.getByRole('button', { name: 'Add Model' }).click();

  await expect(page.getByTestId('ai-batch-progress').getByText('rejected')).toBeVisible();
  const first = batchBodies[0];
  expect(first.configuration).toEqual({ apiKey: 'sk-test' });
  const sent = new Map(first.models.map((item) => [item.model, item]));
  expect(sent.get('gpt-4o')).toMatchObject({ isMultimodal: true, contextLength: 128000 });
  expect(sent.get('gpt-4o-mini')).toMatchObject({ isMultimodal: true, isReasoning: false });
  expect(sent.get('o3')).toMatchObject({ isReasoning: true });

  await expect(page.getByRole('switch', { name: 'Reasoning for gpt-4o' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Retry' }).click();

  await expect(page.getByTestId('ai-batch-progress')).toHaveCount(0);
  expect(batchBodies).toHaveLength(2);
  expect(batchBodies[1].models.map((item) => item.model)).toEqual(['o3']);
  expect(batchBodies[1].connectionId).toBe('conn-e2e');
});

test('a provider without a model list still adds several typed models', async ({ page }) => {
  const batchBodies = await mockAiModels(page, 'manual');
  await page.goto('/workspace/ai-models/');
  await openOpenAI(page);

  await expect(page.getByTestId('ai-manual-models-hint')).toBeVisible();
  await expect(page.getByTestId('ai-fetch-models')).toHaveCount(0);
  await page.getByPlaceholder('Discovery model id').fill('my-deployment');
  await page.getByLabel('Custom model id').fill('my-other-deployment');
  await page.getByRole('button', { name: 'Add', exact: true }).click();

  await page.getByRole('switch', { name: 'Reasoning for my-other-deployment' }).click();
  await page.getByRole('button', { name: 'Add Model' }).click();

  await expect.poll(() => batchBodies.length).toBe(1);
  expect(batchBodies[0].models).toEqual([
    expect.objectContaining({ model: 'my-deployment', isReasoning: false }),
    expect.objectContaining({ model: 'my-other-deployment', isReasoning: true }),
  ]);
});

test('onboarding uses the same model picker', async ({ page }) => {
  await page.route('**/api/v1/org/onboarding-status', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({ json: { status: 'notConfigured' } });
  });
  await mockAiModels(page);
  await page.goto('/onboarding?step=ai-model');
  await pickThreeModels(page);
  await expect(page.getByTestId('ai-picked-models')).toBeVisible();
});
