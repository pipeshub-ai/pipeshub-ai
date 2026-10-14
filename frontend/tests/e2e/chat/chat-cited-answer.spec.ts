/**
 * The core promise of the product, end to end: upload a document, ask about
 * it in chat, get the right answer, and a citation that leads back to it.
 *
 * Nothing is mocked. The document holds a code that exists nowhere else, so an
 * answer containing it can only have come from retrieving this document; a
 * model guessing, or searching the wrong place, cannot produce it.
 */
import { randomBytes } from 'crypto';
import { test, expect } from '../fixtures/api-context.fixture';
import { hasAnsweringModels, NO_MODEL_REASON } from '../helpers/ai-models.helper';
import { createTestKb, deleteTestKb, makeKbName, uploadFileByApi, waitForIndexed } from '../knowledge-base/kb-upload.helpers';

test.describe('Ask about an uploaded document', () => {
  test.describe.configure({ mode: 'serial' });

  const stamp = Date.now().toString(36);
  const code = `E2EFACT-${randomBytes(4).toString('hex').toUpperCase()}`;
  const recordName = `kestrel-launch-memo-${stamp}`;
  let kbId: string | undefined;

  test.beforeAll(async ({ apiContext }) => {
    // Indexing a new file can take minutes on a cold stack.
    test.setTimeout(420_000);
    // The ai-models setup step adds them when credentials are available.
    test.skip(!(await hasAnsweringModels(apiContext)), NO_MODEL_REASON);

    kbId = (await createTestKb(apiContext, makeKbName('cited-answer'))).id;
    const recordId = await uploadFileByApi(kbId, {
      name: `${recordName}.txt`,
      mimeType: 'text/plain',
      buffer: Buffer.from(
        `Internal launch memo for Project Kestrel ${stamp}.\n\n` +
          `After a long debate the team chose the launch codename ${code} for Project Kestrel ${stamp}. ` +
          'Use it in all launch communication.\n',
      ),
    });
    await waitForIndexed(apiContext, recordId);
  });

  test.afterAll(async ({ apiContext }) => {
    if (kbId) await deleteTestKb(apiContext, kbId);
  });

  test('the answer carries the fact from the document and cites it', async ({ page, apiContext }) => {
    test.setTimeout(240_000);
    const available = await apiContext.get('/api/v1/configurationManager/ai-models/available/llm');
    const models = ((await available.json()) as { models?: Array<{ modelName?: string; isDefault?: boolean }> }).models ?? [];
    const modelName = (models.find((m) => m.isDefault) ?? models[0])?.modelName;
    expect(modelName, 'a chat model should be available').toBeTruthy();

    await page.goto('/chat/');
    // Send only once the page has loaded its model; the composer shows the model's name.
    await expect(page.getByText(modelName!, { exact: true }).first()).toBeVisible({ timeout: 30_000 });
    const input = page.locator('textarea').last();
    await input.fill(`What launch codename did the team choose for Project Kestrel ${stamp}?`);
    await input.press('Enter');

    await expect(page.getByText(code).first(), 'the answer should contain the code from the document').toBeVisible({
      timeout: 180_000,
    });

    // The Citation tab lists what the answer cites; it stays disabled when nothing is cited.
    await page.getByText('Citation', { exact: true }).last().click();
    await expect(page.getByText(recordName).first(), 'the uploaded document should be cited').toBeVisible({
      timeout: 15_000,
    });

    await page.getByRole('button', { name: /Open in Collections/ }).first().click();
    await expect(page, 'the citation should open the collection holding the document').toHaveURL(
      new RegExp(`nodeId=${kbId}`),
      { timeout: 20_000 },
    );
  });
});
