import * as fs from 'fs';
import { test } from '../fixtures/api-context.fixture';
import { ADDED_EMBEDDING_FILE, removeModels, type AddedModel } from '../helpers/ai-models.helper';

test('remove the embedding model added before indexing', async ({ apiContext }) => {
  if (!fs.existsSync(ADDED_EMBEDDING_FILE)) return;
  const added = JSON.parse(fs.readFileSync(ADDED_EMBEDDING_FILE, 'utf-8')) as AddedModel[];
  await removeModels(apiContext, added);
  fs.rmSync(ADDED_EMBEDDING_FILE);
});
