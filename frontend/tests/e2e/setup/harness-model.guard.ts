import { request } from '@playwright/test';
import { test as setup } from '../fixtures/base.fixture';
import { apiBaseURL, getAccessToken } from '../fixtures/api-context.fixture';
import { scriptedModelProblem, type OfferedModel } from '../harness/support/scripted-model';

/**
 * The harness lane refuses to start on a stack where a chat turn could reach a model other than the scripted one
 * (a stack people use may default to a paid model): every harness project depends on this check.
 */
setup('harness lane: every turn reaches the scripted model', async () => {
  const api = await request.newContext({
    baseURL: apiBaseURL(),
    extraHTTPHeaders: { Authorization: `Bearer ${getAccessToken()}` },
  });
  try {
    const res = await api.get('/api/v1/configurationManager/ai-models/available/llm');
    if (!res.ok()) throw new Error(`Refusing to run the harness specs: the stack's models could not be read (${res.status()}).`);
    const models = ((await res.json()) as { models?: OfferedModel[] }).models ?? [];
    const problem = scriptedModelProblem(models, process.env);
    if (problem) throw new Error(`Refusing to run the harness specs: ${problem}`);
  } finally {
    await api.dispose();
  }
});
