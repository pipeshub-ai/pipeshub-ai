import * as fs from 'fs';
import { test } from '../fixtures/api-context.fixture';
import {
  ADDED_EMBEDDING_FILE,
  EMBEDDING_ERROR_FILE,
  provisionAnsweringModels,
} from '../helpers/ai-models.helper';

// Runs before every project that uploads or indexes anything. Once the
// built-in model has embedded a record, the backend refuses to switch to
// another embedding model, so the answering tests' model has to be in place
// first. Its teardown removes the model after all of them finish (or leaves
// it, when its vectors are stored).
test('add the embedding model before anything is indexed', async ({ apiContext }) => {
  test.setTimeout(300_000);
  fs.rmSync(EMBEDDING_ERROR_FILE, { force: true });
  try {
    const added = (await provisionAnsweringModels(apiContext, ['embedding'])) ?? [];
    fs.writeFileSync(ADDED_EMBEDDING_FILE, JSON.stringify(added));
  } catch (error) {
    // Let Playwright retry a transient failure; the retry skips a model the
    // failed attempt already added.
    if (test.info().retry < test.info().project.retries) throw error;
    // Failing here would skip every UI test, most of which need no real
    // model; the ai-models step reports it and only the answering tests skip.
    const message = (error as Error).message;
    fs.writeFileSync(EMBEDDING_ERROR_FILE, message);
    test.info().annotations.push({ type: 'warning', description: message });
  }
});
