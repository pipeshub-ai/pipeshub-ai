import * as fs from 'fs';
import { test } from '../fixtures/api-context.fixture';
import { ADDED_MODELS_FILE, EMBEDDING_ERROR_FILE, provisionAnsweringModels } from '../helpers/ai-models.helper';

// Runs once before the tests that need a real model (the `ai` project). Its
// teardown removes what it added after all of them finish, so no test ever
// deletes a model another test is still using.
test('add AI models for answering tests', async ({ apiContext }) => {
  if (fs.existsSync(EMBEDDING_ERROR_FILE)) {
    // Adding it again now would only be refused because the built-in model
    // has embedded since, which hides the reason it failed the first time.
    throw new Error(fs.readFileSync(EMBEDDING_ERROR_FILE, 'utf-8'));
  }
  // Each model is health-checked against the provider before it is saved.
  test.setTimeout(300_000);
  const added = (await provisionAnsweringModels(apiContext)) ?? [];
  fs.writeFileSync(ADDED_MODELS_FILE, JSON.stringify(added));
});
