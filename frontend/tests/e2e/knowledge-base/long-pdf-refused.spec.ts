import { test, expect } from '../fixtures/api-context.fixture';
import { createTestKb, deleteTestKb, makeKbName, uploadFileByApi } from './kb-upload.helpers';
import { longPdfBuffer } from '../support/long-pdf';

/**
 * A PDF over the page limit is not indexed: the file shows the not-supported status and says why.
 *
 * Tagged @smoke, so the e2e-smoke workflow runs it on every pull request (`npm run test:e2e:smoke`).
 *
 * Run with:
 *   npm run test:e2e -- tests/e2e/knowledge-base/long-pdf-refused.spec.ts
 * Needs a running stack (see tests/e2e/README.md). The reason is read from the API and matched by pattern, so
 * the wording can change without this test.
 */
const OVER_LIMIT_PAGES = 501;
const REASON = /This PDF has [\d,]+ pages; PDFs over 500 pages are not indexed\./;

interface RecordView {
  record?: { indexingStatus?: string; reason?: string };
}

test.describe('a PDF over the page limit', () => {
  let kbId = '';

  test.beforeAll(async ({ apiContext }) => {
    kbId = (await createTestKb(apiContext, makeKbName('long-pdf'))).id;
  });

  test.afterAll(async ({ apiContext }) => {
    await deleteTestKb(apiContext, kbId);
  });

  test('is refused with the status and the reason @smoke', async ({ apiContext }) => {
    const recordId = await uploadFileByApi(kbId, {
      name: 'long.pdf',
      mimeType: 'application/pdf',
      buffer: longPdfBuffer(OVER_LIMIT_PAGES),
    });

    let view: RecordView['record'];
    await expect
      .poll(
        async () => {
          const response = await apiContext.get(`/api/v1/knowledgeBase/record/${recordId}`);
          view = ((await response.json()) as RecordView).record;
          return view?.indexingStatus;
        },
        { timeout: 120_000, intervals: [2_000] },
      )
      .toBe('FILE_TYPE_NOT_SUPPORTED');
    expect(view?.reason ?? '').toMatch(REASON);
  });
});
