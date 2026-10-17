import * as fs from 'fs';
import { test } from '../fixtures/api-context.fixture';
import { ADDED_MODELS_FILE, provisionAnsweringModels } from '../helpers/ai-models.helper';

// Runs once before the tests that need a real model (the `ai` project). Its
// teardown removes what it added after all of them finish, so no test ever
// deletes a model another test is still using.
test('add AI models for answering tests', async ({ apiContext }) => {
  // Each model is health-checked against the provider before it is saved.
  test.setTimeout(300_000);
  const added = (await provisionAnsweringModels(apiContext)) ?? [];
  fs.writeFileSync(ADDED_MODELS_FILE, JSON.stringify(added));
});
