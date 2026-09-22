import * as fs from 'fs';
import { test } from '../fixtures/api-context.fixture';
import { ADDED_MODELS_FILE, removeModels, type AddedModel } from '../helpers/ai-models.helper';

test('remove the AI models added for answering tests', async ({ apiContext }) => {
  if (!fs.existsSync(ADDED_MODELS_FILE)) return;
  const added = JSON.parse(fs.readFileSync(ADDED_MODELS_FILE, 'utf-8')) as AddedModel[];
  await removeModels(apiContext, added);
  fs.rmSync(ADDED_MODELS_FILE);
});
