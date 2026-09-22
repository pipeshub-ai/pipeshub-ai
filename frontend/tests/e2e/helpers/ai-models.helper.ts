import type { APIRequestContext } from '@playwright/test';

const MODELS_API = '/api/v1/configurationManager/ai-models';

/** Written by the ai-models setup step, read by its teardown: the models that step added. */
export const ADDED_MODELS_FILE = '.auth/ai-models.json';

type ModelType = 'llm' | 'embedding';
export type AddedModel = { type: ModelType; modelKey: string };

/**
 * Where a real model comes from, mirroring the integration tests'
 * `helper/ai_models_setup.py`: Azure OpenAI when its credentials are set — which
 * is what CI runs on — then OpenAI, or, for local runs, any OpenAI-compatible
 * server at E2E_AI_ENDPOINT.
 *
 * Azure is tried first for the same reason the Python helper tries it first:
 * whichever key the org is given here is the one every answering test spends.
 */
function modelConfig(type: ModelType): { provider: string; configuration: Record<string, string> } | null {
  const endpoint = process.env.E2E_AI_ENDPOINT;
  if (endpoint) {
    return {
      provider: 'openAICompatible',
      configuration: {
        endpoint,
        apiKey: process.env.E2E_AI_API_KEY || 'unused',
        model: (type === 'llm' ? process.env.E2E_AI_LLM_MODEL : process.env.E2E_AI_EMBEDDING_MODEL) || `e2e-${type}`,
      },
    };
  }

  const azure = azureConfig(type);
  if (azure) return azure;

  const apiKey = process.env.TEST_OPENAI_API_KEY || process.env.OPENAI_API_KEY;
  if (!apiKey) return null;
  const model =
    type === 'llm'
      ? process.env.TEST_OPENAI_LLM_MODEL || 'gpt-5.4-nano'
      : process.env.TEST_OPENAI_EMBEDDING_MODEL || 'text-embedding-3-small';
  return { provider: 'openAI', configuration: { model, apiKey } };
}

/**
 * Azure needs an endpoint and a deployment as well as a key, so a half-set
 * environment falls through to the next provider rather than being configured
 * with blanks.
 *
 * The embedding deployment is required rather than falling back to the chat
 * one: a chat deployment fails an embedding health check, and failing there is
 * worse than never claiming Azure at all, because falling through would have
 * found a working provider.
 */
function azureConfig(type: ModelType): { provider: string; configuration: Record<string, string> } | null {
  const apiKey = process.env.TEST_AZURE_OPENAI_API_KEY;
  const endpoint = process.env.TEST_AZURE_OPENAI_ENDPOINT;
  if (!apiKey || !endpoint) return null;

  const llmDeployment = process.env.TEST_AZURE_OPENAI_DEPLOYMENT_NAME;
  const deploymentName =
    type === 'llm'
      ? llmDeployment
      : process.env.TEST_AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME;
  if (!deploymentName) return null;

  const model =
    type === 'llm'
      ? process.env.TEST_AZURE_OPENAI_MODEL || deploymentName
      : process.env.TEST_AZURE_OPENAI_EMBEDDING_MODEL || 'text-embedding-3-small';
  return { provider: 'azureOpenAI', configuration: { endpoint, apiKey, deploymentName, model } };
}

async function listModels(api: APIRequestContext, type: ModelType): Promise<unknown[]> {
  const res = await api.get(`${MODELS_API}/${type}`);
  if (!res.ok()) throw new Error(`listing ${type} models failed: ${res.status()} ${await res.text()}`);
  return ((await res.json()) as { models?: unknown[] }).models ?? [];
}

/** Whether the org has both a chat and an embedding model, which answering needs. */
export async function hasAnsweringModels(api: APIRequestContext): Promise<boolean> {
  return (await listModels(api, 'llm')).length > 0 && (await listModels(api, 'embedding')).length > 0;
}

/** Remove every model in `added`, trying them all before reporting any failure. */
export async function removeModels(api: APIRequestContext, added: AddedModel[]): Promise<void> {
  const failures: string[] = [];
  for (const { type, modelKey } of [...added].reverse()) {
    try {
      const res = await api.delete(`${MODELS_API}/providers/${type}/${modelKey}`);
      if (!res.ok() && res.status() !== 404) failures.push(`${type} ${modelKey}: ${res.status()} ${await res.text()}`);
    } catch (error) {
      failures.push(`${type} ${modelKey}: ${(error as Error).message}`);
    }
  }
  if (failures.length > 0) throw new Error(`removing test models failed:\n${failures.join('\n')}`);
}

/**
 * Add a chat and a cloud embedding model where the org has none, and return
 * what was added. Models already configured are left alone. Returns undefined
 * when a model is missing and no credentials say how to add one. If adding
 * the second fails, the first is removed again before the error is rethrown.
 */
export async function provisionAnsweringModels(api: APIRequestContext): Promise<AddedModel[] | undefined> {
  const added: AddedModel[] = [];
  try {
    for (const type of ['llm', 'embedding'] as const) {
      if ((await listModels(api, type)).length > 0) continue;
      const config = modelConfig(type);
      if (!config) {
        await removeModels(api, added);
        return undefined;
      }
      // The backend health-checks the model before saving it.
      const res = await api.post(`${MODELS_API}/providers`, {
        data: {
          modelType: type,
          provider: config.provider,
          configuration: config.configuration,
          isMultimodal: false,
          isReasoning: false,
          isDefault: true,
          contextLength: null,
        },
        timeout: 120_000,
      });
      if (!res.ok()) throw new Error(`adding the test ${type} model failed: ${res.status()} ${await res.text()}`);
      const modelKey = ((await res.json()) as { details?: { modelKey?: string } }).details?.modelKey;
      if (!modelKey) throw new Error(`adding the test ${type} model returned no model key`);
      added.push({ type, modelKey });
    }
  } catch (error) {
    await removeModels(api, added);
    throw error;
  }
  return added;
}

export const NO_MODEL_REASON =
  'No AI model is configured and neither TEST_OPENAI_API_KEY nor E2E_AI_ENDPOINT is set; answering needs a real model';
