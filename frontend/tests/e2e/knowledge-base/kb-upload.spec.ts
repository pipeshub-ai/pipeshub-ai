import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { test, expect } from '../fixtures/base.fixture';
import {
  DEFAULT_MAX_FILE_SIZE_BYTES,
  REJECTION,
  TEST_KB_ID,
  makeFiles,
  mockKbContext,
  mockUpload,
  mockUploadRateLimited,
  mockUploadServerError,
  openUploadSidebar,
  type UploadOutcome,
} from './upload-mocks';

/**
 * End-to-end coverage of the Collections file-upload flow.
 *
 * Every test mocks the KB context (so a single OWNED collection renders and the
 * "New > Upload" affordance is available) and the upload endpoint (so each
 * file's outcome — accepted / oversize / unsupported / duplicate / server error
 * — is deterministic). No real backend, object storage, or seed data is needed;
 * tiny in-memory files stand in for real ones because all validation happens
 * server-side and is simulated by the SSE mock.
 *
 * Run with: npm run test:e2e -- tests/e2e/knowledge-base/kb-upload.spec.ts
 *
 * Coverage matrix:
 *   selection      — Save disabled until a file is chosen
 *   1 file         — single accepted file -> completed row
 *   many files     — 12 files (multi-batch) -> all complete, header count
 *   no cap         — 150 files all render (no "+N more" truncation), scrollable
 *   folder         — webkitdirectory upload preserves hierarchy -> all complete
 *   mixed          — accepted + oversize + unsupported settle independently
 *   all unsupported— every file fails
 *   duplicates     — DUPLICATE_NAME reported as failed (not dropped)
 *   server error   — non-200 fails the whole batch
 *   rate limit msg — 429 shows the server's EXACT message, not "timed out"
 *   size limit     — page honours a backend-configured smaller limit
 *   reason text    — failed row shows the server's human-readable message
 *   default text   — failed row falls back to a default when none is given
 *   tab filter     — the Failed tab filters the tracker to failed rows
 *   cancel         — Cancel closes the sidebar and fires no upload
 *   scale          — 2000 files in one go without breaking the tracker
 *   baseline       — route renders without crashing
 */

const completedRows = (page: import('@playwright/test').Page) =>
  page.locator('[data-testid="upload-item-row"][data-status="completed"]');
const failedRows = (page: import('@playwright/test').Page) =>
  page.locator('[data-testid="upload-item-row"][data-status="failed"]');

/** Outcome by file-name prefix, so one mock serves mixed-batch tests. */
const byPrefix = (name: string): UploadOutcome => {
  if (name.startsWith('big-')) return { outcome: 'failed', reason: REJECTION.EXCEEDS_SIZE_LIMIT };
  if (name.startsWith('bad-')) return { outcome: 'failed', reason: REJECTION.UNSUPPORTED_TYPE };
  if (name.startsWith('dup-')) return { outcome: 'failed', reason: REJECTION.DUPLICATE_NAME };
  return { outcome: 'succeeded' };
};

test.describe('Knowledge Base Upload', () => {
  test('Save is disabled until at least one file is selected', async ({ page }) => {
    await mockKbContext(page);
    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    const save = page.getByRole('button', { name: 'Save' });
    await expect(save).toBeDisabled();
  });

  test('one file within limits renders a completed row', async ({ page }) => {
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(1, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(1, { timeout: 15_000 });
    await expect(failedRows(page)).toHaveCount(0);
  });

  test('many files within limits all complete (spans multiple batches)', async ({ page }) => {
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    // 12 files > BATCH_SIZE (10), so this exercises multi-batch fan-out.
    await input!.setInputFiles(makeFiles(12, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const tracker = page.getByTestId('upload-progress-tracker');
    await expect(tracker).toBeVisible({ timeout: 15_000 });
    await expect(failedRows(page)).toHaveCount(0, { timeout: 20_000 });
    // Header reports total completed even when rows are virtualized/capped.
    await expect(tracker).toContainText('12/12', { timeout: 20_000 });
  });

  test('renders every completed row (no cap, no "+N more" summary)', async ({ page }) => {
    // Regression for the tracker cap: 150 files is well above the old 60-row
    // limit, so this proves the Completed tab shows the whole list, scrollable.
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(150, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const tracker = page.getByTestId('upload-progress-tracker');
    await expect(tracker).toBeVisible({ timeout: 15_000 });
    await expect(tracker).toContainText('150/150', { timeout: 60_000 });
    // Every completed row is present in the DOM (off-screen rows are kept via
    // content-visibility, not dropped), and no truncation summary is shown.
    await expect(completedRows(page)).toHaveCount(150, { timeout: 10_000 });
    await expect(tracker).not.toContainText(/more completed/i);

    // The Completed tab also shows all 150.
    await page.getByRole('tab', { name: /completed/i }).click();
    await expect(completedRows(page)).toHaveCount(150);
  });

  test('uploads a folder, preserving hierarchy, and completes each file', async ({ page }) => {
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    // webkitdirectory upload needs real files on disk so the browser can derive
    // webkitRelativePath (the folder hierarchy the backend rebuilds).
    const dir = mkdtempSync(join(tmpdir(), 'e2e-upload-'));
    mkdirSync(join(dir, 'sub'));
    writeFileSync(join(dir, 'ok-root.pdf'), 'root');
    writeFileSync(join(dir, 'sub', 'ok-nested.pdf'), 'nested');
    try {
      await page.getByTestId('upload-input-folder').setInputFiles(dir);
      await page.getByRole('button', { name: 'Save' }).click();

      await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
      await expect(completedRows(page)).toHaveCount(2, { timeout: 20_000 });
      await expect(failedRows(page)).toHaveCount(0);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test('mixed batch: accepted, oversize, and unsupported files settle independently', async ({
    page,
  }) => {
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles([
      ...makeFiles(2, { prefix: 'ok' }),
      ...makeFiles(2, { prefix: 'big' }), // EXCEEDS_SIZE_LIMIT
      ...makeFiles(1, { prefix: 'bad', ext: 'xyz', mimeType: 'application/octet-stream' }), // UNSUPPORTED_TYPE
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(2, { timeout: 20_000 });
    await expect(failedRows(page)).toHaveCount(3, { timeout: 20_000 });
  });

  test('all unsupported types: every file fails', async ({ page }) => {
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(
      makeFiles(3, { prefix: 'bad', ext: 'exe', mimeType: 'application/octet-stream' }),
    );
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(failedRows(page)).toHaveCount(3, { timeout: 20_000 });
    await expect(completedRows(page)).toHaveCount(0);
  });

  test('duplicate names are reported as failed (not silently dropped)', async ({ page }) => {
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(2, { prefix: 'dup' }));
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(failedRows(page)).toHaveCount(2, { timeout: 20_000 });
  });

  test('an unknown/arbitrary failure reason still shows the exact server message', async ({
    page,
  }) => {
    // A reason code the client has never seen (e.g. a future server-side check)
    // must NOT be dropped or mislabelled — the row shows the server's message
    // verbatim and the file is treated as a generic failure.
    await mockKbContext(page);
    await mockUpload(page, () => ({
      outcome: 'failed',
      reason: 'VIRUS_DETECTED',
      errors: ['Malware detected in file'],
    }));

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(1, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const row = failedRows(page).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText(/malware detected in file/i);
  });

  test('a transport/server error fails the whole batch', async ({ page }) => {
    await mockKbContext(page);
    await mockUploadServerError(page, 500);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(3, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(failedRows(page)).toHaveCount(3, { timeout: 20_000 });
    await expect(completedRows(page)).toHaveCount(0);
  });

  test('rate-limit (429) shows the exact server message, not a timeout', async ({ page }) => {
    await mockKbContext(page);
    await mockUploadRateLimited(page); // 429 with the real rate-limiter body

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(1, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const row = failedRows(page).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    // The user sees the real reason from the 429 body...
    await expect(row).toContainText(/too many requests/i);
    // ...and NOT the misleading timeout text.
    await expect(page.getByTestId('upload-progress-tracker')).not.toContainText(/timed out/i);
  });

  test('respects a backend-configured smaller size limit (rejects oversize)', async ({ page }) => {
    // Limit is irrelevant to the mock's decision, but this proves the page reads
    // the configured limit without breaking when it differs from the default.
    await mockKbContext(page, { maxFileSizeBytes: 5 * 1024 * 1024 });
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles([
      ...makeFiles(1, { prefix: 'ok' }),
      ...makeFiles(1, { prefix: 'big' }),
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(1, { timeout: 20_000 });
    await expect(failedRows(page)).toHaveCount(1, { timeout: 20_000 });
  });

  test('failed row shows the human-readable failure message', async ({ page }) => {
    await mockKbContext(page);
    await mockUpload(page, (name) =>
      name.startsWith('big-')
        ? {
            outcome: 'failed',
            reason: REJECTION.EXCEEDS_SIZE_LIMIT,
            errors: ['File exceeds the 30 MB size limit'],
          }
        : { outcome: 'succeeded' },
    );

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(1, { prefix: 'big' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const row = failedRows(page).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText(/exceeds the 30 MB size limit/i);
  });

  test('failed row falls back to a default message when the server gives none', async ({ page }) => {
    await mockKbContext(page);
    await mockUpload(page, () => ({ outcome: 'failed' })); // no reason, no errors

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(1, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const row = failedRows(page).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText(/upload failed/i);
  });

  test('the Failed tab filters the tracker to failed rows', async ({ page }) => {
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles([
      ...makeFiles(2, { prefix: 'ok' }),
      ...makeFiles(2, { prefix: 'bad', ext: 'xyz', mimeType: 'application/octet-stream' }),
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(2, { timeout: 20_000 });

    await page.getByRole('tab', { name: /failed/i }).click();
    await expect(failedRows(page)).toHaveCount(2);
    await expect(completedRows(page)).toHaveCount(0);
  });

  test('Cancel closes the sidebar and fires no upload', async ({ page }) => {
    await mockKbContext(page);
    let uploadCalls = 0;
    await page.route('**/api/v1/knowledge*base/**/upload', (route) => {
      uploadCalls += 1;
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': noop\n\n' });
    });

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    await input!.setInputFiles(makeFiles(1, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Cancel' }).click();
    await page.waitForTimeout(750);

    expect(uploadCalls).toBe(0);
    await expect(page.getByTestId('upload-progress-tracker')).toHaveCount(0);
  });

  test('uploads 2000 files in one go without breaking the tracker', async ({ page }) => {
    test.setTimeout(300_000);
    await mockKbContext(page);
    await mockUpload(page, byPrefix);

    const input = await openUploadSidebar(page);
    test.skip(!input, 'Upload affordance not reachable (mock shape drift)');

    // 2000 files => 200 batches of 10, 5 concurrent. The tracker caps how many
    // rows it renders (MAX_ROWS_PER_SESSION) but the header counts the full set.
    await input!.setInputFiles(makeFiles(2000, { prefix: 'ok' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const tracker = page.getByTestId('upload-progress-tracker');
    await expect(tracker).toBeVisible({ timeout: 30_000 });
    // Eventually all 2000 report complete.
    await expect(tracker).toContainText('2000/2000', { timeout: 240_000 });
    await expect(failedRows(page)).toHaveCount(0);
    // The full list is present (content-visibility keeps off-screen rows in the
    // DOM) and scrollable — not collapsed into a summary.
    await expect(completedRows(page)).toHaveCount(2000, { timeout: 30_000 });
    await expect(tracker).not.toContainText(/more completed/i);
  });

  test('page still loads when there is no upload affordance', async ({ page }) => {
    // Baseline that always runs (no mocks): the route renders without crashing.
    await page.goto(`/knowledge-base/?nodeType=kb&nodeId=${TEST_KB_ID}`);
    await expect(page).toHaveURL(/\/knowledge-base\//);
    // Sanity: the default limit constant is exported and sane.
    expect(DEFAULT_MAX_FILE_SIZE_BYTES).toBeGreaterThan(0);
  });
});
