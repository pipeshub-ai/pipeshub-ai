import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { test, expect } from '../fixtures/api-context.fixture';
import {
  DEFAULT_MAX_FILE_SIZE_BYTES,
  REJECTION,
  makeKbName,
  createTestKb,
  deleteTestKb,
  fetchMaxFileSizeBytes,
  makeFiles,
  makeOversizeFile,
  openUploadSidebar,
} from './kb-upload.helpers';

/**
 * End-to-end coverage of the Collections file-upload flow against a real backend.
 *
 * Each describe block owns its KB lifecycle: a real KB is created before the
 * tests run and deleted after. File uploads hit the actual Node.js API, which
 * streams SSE per-file outcomes back to the browser. No network interception is
 * used — every outcome (success, oversize rejection, unsupported type, duplicate)
 * is driven by the real server-side validation.
 *
 * Run with:
 *   npm run test:e2e -- tests/e2e/knowledge-base/kb-upload.spec.ts
 *
 * Prerequisites:
 *   - Backend running at NEXT_PUBLIC_API_BASE_URL (default http://localhost:3000)
 *   - Frontend running at BASE_URL (default http://localhost:3001)
 *   - Auth state in .auth/user.json (run the setup project first)
 *
 * Coverage:
 *   selection             — Save disabled until a file is chosen
 *   1 file                — single accepted file → completed row
 *   multi-batch           — 12 files (> BATCH_SIZE=10) → all complete, header count
 *   folder                — webkitdirectory upload preserves hierarchy → all complete
 *   mixed                 — accepted + oversize + unsupported settle independently
 *   all unsupported       — every file fails with UNSUPPORTED_TYPE
 *   duplicates            — DUPLICATE_NAME reported as failed
 *   reason text           — failed row shows the server's rejection message
 *   tab filter            — Failed tab filters the tracker to failed rows
 *   cancel                — Cancel closes sidebar and fires no upload request
 *   scale                 — 50 files in one go without breaking the tracker
 *   multi-dot filename    — last-dot extension rule (report.v2.pdf → ext=pdf)
 *   within-batch dup      — second copy of same name in one batch fails
 *   at-size-limit         — exactly maxBytes passes the size check
 *   special chars         — spaces, hyphens, parens in filename
 *   mixed folder types    — pdf completes, xyz fails in same folder upload
 *   All tab default       — activeTab initialises to 'all'
 *   Failed tab count      — tab badge shows correct failed count
 *   both inputs           — file and folder inputs both attached when sidebar open
 *   extension-in-name     — pdf.pdf, report.pdf.pdf, document.txt.pdf all complete
 *   3-file 2-same-name    — [a, b, a] → 2 complete, 1 fails as duplicate
 *   baseline              — route renders without crashing
 */

const completedRows = (page: import('@playwright/test').Page) =>
  page.locator('[data-testid="upload-item-row"][data-status="completed"]');
const failedRows = (page: import('@playwright/test').Page) =>
  page.locator('[data-testid="upload-item-row"][data-status="failed"]');

// ---------------------------------------------------------------------------
// Main suite — shared KB for all non-duplicate tests
// ---------------------------------------------------------------------------

test.describe('Knowledge Base Upload', () => {
  // serial: all tests share one worker → beforeAll runs once → one KB created.
  // Without this, fullyParallel distributes tests across workers and each
  // worker runs beforeAll independently, creating dozens of KBs.
  test.describe.configure({ mode: 'serial' });

  let kbId: string;
  let realMaxBytes: number;

  test.beforeAll(async ({ apiContext }) => {
    const kb = await createTestKb(apiContext, makeKbName('main'));
    kbId = kb.id;
    realMaxBytes = await fetchMaxFileSizeBytes(apiContext);
  });

  test.afterAll(async ({ apiContext }) => {
    await deleteTestKb(apiContext, kbId);
  });

  test('Save is disabled until at least one file is selected', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    const save = page.getByRole('button', { name: 'Save' });
    await expect(save).toBeDisabled();
  });

  test('one file within limits renders a completed row', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    await input!.setInputFiles(makeFiles(1, { prefix: 'ok-single' }));
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(1, { timeout: 30_000 });
    await expect(failedRows(page)).toHaveCount(0);
  });

  test('12 files across multiple batches all complete', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // 12 > BATCH_SIZE (10), so this exercises multi-batch fan-out.
    await input!.setInputFiles(makeFiles(12, { prefix: 'ok-multi' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const tracker = page.getByTestId('upload-progress-tracker');
    await expect(tracker).toBeVisible({ timeout: 15_000 });
    await expect(failedRows(page)).toHaveCount(0, { timeout: 60_000 });
    await expect(tracker).toContainText('12/12', { timeout: 60_000 });
  });

  test('uploads a folder, preserving hierarchy, and completes each file', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // webkitdirectory upload needs real files on disk so the browser can derive
    // webkitRelativePath (the folder hierarchy the backend reconstructs).
    const dir = mkdtempSync(join(tmpdir(), 'e2e-upload-'));
    mkdirSync(join(dir, 'sub'));
    writeFileSync(join(dir, 'ok-folder-root.pdf'), 'root content');
    writeFileSync(join(dir, 'sub', 'ok-folder-nested.pdf'), 'nested content');
    try {
      await page.getByTestId('upload-input-folder').setInputFiles(dir);
      await page.getByRole('button', { name: 'Save' }).click();

      await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
      await expect(completedRows(page)).toHaveCount(2, { timeout: 30_000 });
      await expect(failedRows(page)).toHaveCount(0);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test('mixed batch: accepted, oversize, and unsupported files settle independently', async ({
    page,
  }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    const oversize = makeOversizeFile(realMaxBytes);

    await input!.setInputFiles([
      ...makeFiles(2, { prefix: 'ok-mixed' }),
      oversize,
      ...makeFiles(1, { prefix: 'bad-mixed', ext: 'xyz', mimeType: 'application/octet-stream' }),
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    // 2 valid files complete; 1 oversize + 1 unsupported type fail.
    await expect(completedRows(page)).toHaveCount(2, { timeout: 60_000 });
    await expect(failedRows(page)).toHaveCount(2, { timeout: 60_000 });
  });

  test('all unsupported types: every file is rejected', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // .exe is in extensionToMimeType → backend accepts it.
    // .xyz is not in the map → rejected as UNSUPPORTED_TYPE.
    await input!.setInputFiles(
      makeFiles(3, { prefix: 'bad-all', ext: 'xyz', mimeType: 'application/octet-stream' }),
    );
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(failedRows(page)).toHaveCount(3, { timeout: 30_000 });
    await expect(completedRows(page)).toHaveCount(0);
  });

  test('failed row shows the server rejection message for an oversized file', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    await input!.setInputFiles([makeOversizeFile(realMaxBytes)]);
    await page.getByRole('button', { name: 'Save' }).click();

    const row = failedRows(page).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    // The row must display the human-readable rejection message from the server.
    await expect(row).toContainText(/exceed|size|limit/i, { timeout: 30_000 });
  });

  test('the Failed tab filters the tracker to failed rows', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    await input!.setInputFiles([
      ...makeFiles(2, { prefix: 'ok-tab' }),
      ...makeFiles(2, { prefix: 'bad-tab', ext: 'xyz', mimeType: 'application/octet-stream' }),
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(2, { timeout: 30_000 });

    await page.getByRole('tab', { name: /failed/i }).click();
    await expect(failedRows(page)).toHaveCount(2);
    await expect(completedRows(page)).toHaveCount(0);
  });

  test('Cancel closes the sidebar and fires no upload', async ({ page }) => {
    // Count requests that actually reach the upload endpoint before cancelling.
    let uploadCalls = 0;
    page.on('request', (req) => {
      if (/knowledgebase.*upload/i.test(req.url())) uploadCalls += 1;
    });

    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    await input!.setInputFiles(makeFiles(1, { prefix: 'ok-cancel' }));
    await page.getByRole('button', { name: 'Cancel' }).click();
    await page.waitForTimeout(750);

    expect(uploadCalls).toBe(0);
    await expect(page.getByTestId('upload-progress-tracker')).toHaveCount(0);
  });

  test('uploads 50 files in one go without breaking the tracker', async ({ page }) => {
    test.setTimeout(150_000); // ~5s sidebar + 90s 50-file upload + 55s headroom
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // 50 files → 5 batches of 10, 5 concurrent. Verifies the tracker handles
    // a larger set without row-cap truncation or counter desync.
    await input!.setInputFiles(makeFiles(50, { prefix: 'ok-scale' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const tracker = page.getByTestId('upload-progress-tracker');
    await expect(tracker).toBeVisible({ timeout: 15_000 });
    await expect(tracker).toContainText('50/50', { timeout: 90_000 });
    await expect(failedRows(page)).toHaveCount(0);
  });

  test('file with a multi-dot name completes (last-dot extension rule)', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // "v2" is NOT the extension — "pdf" (after the last dot) is.
    // Regression guard: an earlier bug extracted "v2" as the extension and
    // rejected the file as an unsupported type.
    await input!.setInputFiles([
      { name: 'report.v2.pdf', mimeType: 'application/pdf', buffer: Buffer.from('multi-dot') },
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(1, { timeout: 30_000 });
    await expect(failedRows(page)).toHaveCount(0);
  });

  test('within-batch duplicate: second copy of same filename fails', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // Two entries with identical names in one setInputFiles call exercises the
    // Python service's within-batch duplicate detection. The first copy must
    // complete; the second must fail with a DUPLICATE_NAME message.
    await input!.setInputFiles([
      { name: 'within-batch-dup.pdf', mimeType: 'application/pdf', buffer: Buffer.from('copy A') },
      { name: 'within-batch-dup.pdf', mimeType: 'application/pdf', buffer: Buffer.from('copy B') },
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(1, { timeout: 30_000 });
    await expect(failedRows(page)).toHaveCount(1, { timeout: 30_000 });
    await expect(failedRows(page).first()).toContainText(/already exists/i);
  });

  test('file at exactly the size limit is not rejected as oversized', async ({ page }) => {
    test.setTimeout(120_000);
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // Boundary: the size check is strictly greater-than, so exactly realMaxBytes
    // must pass the file processor. Whether indexing succeeds is separate.
    await input!.setInputFiles([
      { name: 'at-limit.pdf', mimeType: 'application/pdf', buffer: Buffer.alloc(realMaxBytes) },
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    const tracker = page.getByTestId('upload-progress-tracker');
    await expect(tracker).toBeVisible({ timeout: 15_000 });

    // Wait for the row to leave "uploading" state (completes or fails at indexing).
    await expect(
      page.locator('[data-testid="upload-item-row"][data-status="uploading"]'),
    ).toHaveCount(0, { timeout: 90_000 });

    // Size-limit rejections emit a specific phrase; it must not appear here.
    await expect(tracker).not.toContainText(/exceed|size limit/i);
  });

  test('filename with spaces and special characters completes', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    await input!.setInputFiles([
      { name: 'my report (draft) v2.pdf', mimeType: 'application/pdf', buffer: Buffer.from('spaces') },
      { name: 'file-with-hyphens_and_underscores.pdf', mimeType: 'application/pdf', buffer: Buffer.from('hyphens') },
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(2, { timeout: 30_000 });
    await expect(failedRows(page)).toHaveCount(0);
  });

  test('folder upload with mixed types: pdf files complete, xyz files fail', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    const dir = mkdtempSync(join(tmpdir(), 'e2e-mixed-'));
    writeFileSync(join(dir, 'mixed-ok-0.pdf'), 'valid pdf');
    writeFileSync(join(dir, 'mixed-ok-1.pdf'), 'valid pdf 2');
    writeFileSync(join(dir, 'mixed-bad-0.xyz'), 'unsupported');
    writeFileSync(join(dir, 'mixed-bad-1.xyz'), 'unsupported 2');
    try {
      await page.getByTestId('upload-input-folder').setInputFiles(dir);
      await page.getByRole('button', { name: 'Save' }).click();

      await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
      await expect(completedRows(page)).toHaveCount(2, { timeout: 30_000 });
      await expect(failedRows(page)).toHaveCount(2, { timeout: 30_000 });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test('All tab is selected by default when tracker opens', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    await input!.setInputFiles(makeFiles(1, { prefix: 'tab-default' }));
    await page.getByRole('button', { name: 'Save' }).click();

    const tracker = page.getByTestId('upload-progress-tracker');
    await expect(tracker).toBeVisible({ timeout: 15_000 });

    // activeTab initialises to 'all'; aria-selected="true" confirms it.
    const allTab = tracker.getByRole('tab', { name: /all/i });
    await expect(allTab).toBeVisible();
    await expect(allTab).toHaveAttribute('aria-selected', 'true');
  });

  test('Failed tab badge shows the correct failed count', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    await input!.setInputFiles(
      makeFiles(3, { prefix: 'tab-badge-bad', ext: 'xyz', mimeType: 'application/octet-stream' }),
    );
    await page.getByRole('button', { name: 'Save' }).click();

    const tracker = page.getByTestId('upload-progress-tracker');
    await expect(tracker).toBeVisible({ timeout: 15_000 });
    await expect(failedRows(page)).toHaveCount(3, { timeout: 30_000 });

    // Tab label format: "{label} ({count})" — e.g. "Failed (3)".
    const failedTab = tracker.getByRole('tab', { name: /failed/i });
    await expect(failedTab).toContainText('(3)');
  });

  test('upload sidebar renders both File and Folder inputs', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // Both inputs must be attached when the sidebar is open.
    // If either disappears the entire upload flow breaks silently.
    await expect(page.getByTestId('upload-input-file')).toBeAttached();
    await expect(page.getByTestId('upload-input-folder')).toBeAttached();
  });

  test('files whose name contains a known extension all complete', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // Regression for the hasExtension storage-guard bug that incorrectly
    // rejected files whose document name ends with a known extension string.
    // The real extension is always the segment after the last dot (last-dot rule).
    await input!.setInputFiles([
      { name: 'pdf.pdf',          mimeType: 'application/pdf', buffer: Buffer.from('a') },
      { name: 'report.pdf.pdf',   mimeType: 'application/pdf', buffer: Buffer.from('b') },
      { name: 'document.txt.pdf', mimeType: 'application/pdf', buffer: Buffer.from('c') },
      { name: 'v2-test.docx.pdf', mimeType: 'application/pdf', buffer: Buffer.from('d') },
      { name: 'v2-test.pdf.docx.pdf', mimeType: 'application/pdf', buffer: Buffer.from('e') },

    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(5, { timeout: 30_000 });
    await expect(failedRows(page)).toHaveCount(0);
  });

  test('page still loads when there is no upload affordance', async ({ page }) => {
    // Baseline: the route renders without crashing even before any KB is selected.
    await page.goto('/knowledge-base/');
    await expect(page).toHaveURL(/\/knowledge-base\//);
    // Sanity: the default limit constant is a positive number.
    expect(DEFAULT_MAX_FILE_SIZE_BYTES).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Duplicate detection — isolated KB so the first upload is fresh
// ---------------------------------------------------------------------------

test.describe('Knowledge Base Upload — Duplicate Detection', () => {
  test.describe.configure({ mode: 'serial' });

  let kbId: string;

  test.beforeAll(async ({ apiContext }) => {
    const kb = await createTestKb(apiContext, makeKbName('dup'));
    kbId = kb.id;
  });

  test.afterAll(async ({ apiContext }) => {
    await deleteTestKb(apiContext, kbId);
  });

  test('duplicate names are reported as failed', async ({ page }) => {
    // First upload: file is accepted.
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    await input!.setInputFiles(makeFiles(1, { prefix: 'dup' }));
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(1, { timeout: 30_000 });

    // Second upload: same file name → backend emits DUPLICATE_NAME.
    const input2 = await openUploadSidebar(page, kbId);
    test.skip(!input2, 'Upload affordance not reachable for second upload');

    await input2!.setInputFiles(makeFiles(1, { prefix: 'dup' }));
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    const dupRow = failedRows(page).first();
    await expect(dupRow).toBeVisible({ timeout: 30_000 });
    // The backend emits the human-readable message (not the raw reason code) in
    // the SSE file:failed event; the frontend displays it verbatim in the row.
    // "already exists" matches both the backend message and the i18n fallback.
    await expect(dupRow).toContainText(/already exists/i);
  });

  test('three files where two share a name: two complete, one fails as duplicate', async ({ page }) => {
    const input = await openUploadSidebar(page, kbId);
    test.skip(!input, 'Upload affordance not reachable');

    // Batch of 3: first and third share a name. The Python service processes
    // them in order; the third is detected as a within-batch duplicate.
    await input!.setInputFiles([
      { name: 'triple-0.pdf', mimeType: 'application/pdf', buffer: Buffer.from('first') },
      { name: 'triple-1.pdf', mimeType: 'application/pdf', buffer: Buffer.from('second') },
      { name: 'triple-0.pdf', mimeType: 'application/pdf', buffer: Buffer.from('third') },
    ]);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByTestId('upload-progress-tracker')).toBeVisible({ timeout: 15_000 });
    await expect(completedRows(page)).toHaveCount(2, { timeout: 30_000 });
    await expect(failedRows(page)).toHaveCount(1, { timeout: 30_000 });
    await expect(failedRows(page).first()).toContainText(/already exists/i);
  });
});
