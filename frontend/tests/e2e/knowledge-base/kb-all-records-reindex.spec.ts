import type { APIRequestContext } from '@playwright/test';
import { test, expect } from '../fixtures/api-context.fixture';
import { makeKbName, createTestKb, deleteTestKb, uploadFileByApi } from './kb-upload.helpers';

// Unfinished JSON fails parsing as a terminal error, so the record lands in
// FAILED on its first attempt without breaking anything else on the stack.
const BROKEN_JSON = Buffer.from('{"inventory": [1, 2, ');

async function waitForFailed(apiContext: APIRequestContext, recordId: string, timeoutMs = 180_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let status = 'unknown';
  while (Date.now() < deadline) {
    const response = await apiContext.get(`/api/v1/knowledgeBase/record/${recordId}`);
    if (!response.ok()) {
      throw new Error(`reading record ${recordId} failed [${response.status()}]: ${await response.text()}`);
    }
    status = ((await response.json()) as { record?: { indexingStatus?: string } }).record?.indexingStatus ?? 'unknown';
    if (status === 'FAILED') return;
    if (status === 'COMPLETED' || status === 'EMPTY') {
      throw new Error(`record ${recordId} ended ${status}; the broken JSON fixture was expected to fail indexing`);
    }
    await new Promise((r) => setTimeout(r, 3_000));
  }
  throw new Error(`record ${recordId} was still ${status} after ${timeoutMs / 1000}s, not FAILED`);
}

test.describe('All Records reindex', () => {
  test.describe.configure({ mode: 'serial' });

  let kb: { id: string; name: string };
  let recordId: string;
  const fileName = `broken-${Date.now()}.json`;

  test.beforeAll(async ({ apiContext }) => {
    kb = await createTestKb(apiContext, makeKbName('reindex'));
    recordId = await uploadFileByApi(kb.id, { name: fileName, mimeType: 'application/json', buffer: BROKEN_JSON });
    await waitForFailed(apiContext, recordId);
  });

  test.afterAll(async ({ apiContext }) => {
    if (kb) await deleteTestKb(apiContext, kb.id);
  });

  test('Retry indexing on a failed record sends the reindex and confirms it', async ({ page }) => {
    await page.goto(`/knowledge-base?view=all-records&nodeType=app&nodeId=${kb.id}`);

    const row = page.getByRole('row', { name: fileName });
    await expect(row, 'the failed record should be listed in All Records').toBeVisible({ timeout: 30_000 });
    // The trigger's only text is its icon ligature.
    await row.getByRole('button', { name: 'more_horiz' }).click();

    const reindex = page.waitForRequest(
      (r) => r.method() === 'POST' && r.url().includes(`/api/v1/knowledgeBase/reindex/record/${recordId}`),
    );
    await page.getByRole('menuitem', { name: /Retry indexing/ }).click();

    const request = await reindex;
    expect(request.postDataJSON()).toEqual({ depth: 0 });
    const response = await request.response();
    expect(response?.status(), 'the reindex request should be accepted').toBe(200);
    await expect(page.getByText('Successfully queued for reindexing').first()).toBeVisible({ timeout: 15_000 });
  });
});
