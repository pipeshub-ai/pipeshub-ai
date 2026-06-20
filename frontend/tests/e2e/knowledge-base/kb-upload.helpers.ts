import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { mkdtempSync, writeFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { postWithRetry } from '../helpers/api-retry.helper';

/** Stable reason codes emitted by the backend in `file:failed` SSE events. */
export const REJECTION = {
  EXCEEDS_SIZE_LIMIT: 'EXCEEDS_SIZE_LIMIT',
  UNSUPPORTED_TYPE: 'UNSUPPORTED_TYPE',
  DUPLICATE_NAME: 'DUPLICATE_NAME',
} as const;

/** Default max file size — exported for the baseline sanity assertion. */
export const DEFAULT_MAX_FILE_SIZE_BYTES = 30 * 1024 * 1024;

export const KB_NAME_PREFIX = 'E2E Upload';

/** Generate a unique KB name with an optional label suffix. */
export function makeKbName(label = ''): string {
  const stamp = Date.now();
  return label ? `${KB_NAME_PREFIX} ${stamp}-${label}` : `${KB_NAME_PREFIX} ${stamp}`;
}

/** Create a KB via the real API. Returns the new KB's id. */
export async function createTestKb(
  apiContext: APIRequestContext,
  name: string,
): Promise<{ id: string; name: string }> {
  const response = await postWithRetry(apiContext, '/api/v1/knowledgeBase', { kbName: name });
  if (!response.ok()) {
    const body = await response.text();
    throw new Error(`createTestKb failed [${response.status()}]: ${body}`);
  }
  const data = await response.json();
  const id: string | undefined =
    data.id ?? data._key ?? data.kb?.id ?? data.kb?._key;
  if (!id) throw new Error(`createTestKb: no id in response: ${JSON.stringify(data)}`);
  return { id, name };
}

/** Delete a KB via the real API. Best-effort — does not throw on failure. */
export async function deleteTestKb(
  apiContext: APIRequestContext,
  kbId: string,
): Promise<void> {
  await apiContext.delete(`/api/v1/knowledgeBase/${kbId}`).catch(() => undefined);
}

/**
 * Fetch the real configured upload size limit from the backend.
 * Falls back to DEFAULT_MAX_FILE_SIZE_BYTES on any error.
 */
export async function fetchMaxFileSizeBytes(
  apiContext: APIRequestContext,
): Promise<number> {
  try {
    const response = await apiContext.get('/api/v1/knowledgeBase/limits');
    if (response.ok()) {
      const data = await response.json();
      const bytes: unknown = data.maxFileSizeBytes;
      if (typeof bytes === 'number' && bytes > 0) return bytes;
    }
  } catch {
    // fall through to default
  }
  return DEFAULT_MAX_FILE_SIZE_BYTES;
}

// ---------------------------------------------------------------------------
// Valid PDF generation
// ---------------------------------------------------------------------------

/**
 * Build a structurally valid single-page PDF of approximately `targetBytes`.
 *
 * The output contains a proper header (%PDF-1.4), indirect objects (Catalog,
 * Pages, Page, content stream), a cross-reference table with correct byte
 * offsets, and a trailer — so any PDF reader can open it without errors.
 *
 * Size is controlled by padding the content stream. The structural overhead
 * is ~350 bytes, so targets below that produce the minimum valid PDF.
 */
export function validPdfBuffer(targetBytes: number, label = 'test'): Buffer {
  const header = '%PDF-1.4\n';
  const obj1 = '1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n';
  const obj2 = '2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n';
  const obj3 =
    '3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n';

  const off1 = header.length;
  const off2 = off1 + obj1.length;
  const off3 = off2 + obj2.length;
  const off4 = off3 + obj3.length;

  const structuralOverhead = 350;
  const streamLen = Math.max(1, targetBytes - structuralOverhead);

  const obj4Pre = `4 0 obj\n<< /Length ${streamLen} >>\nstream\n`;
  const obj4Post = '\nendstream\nendobj\n';

  const padding = Buffer.alloc(streamLen, 0x20);

  const body = Buffer.concat([
    Buffer.from(header + obj1 + obj2 + obj3 + obj4Pre),
    padding,
    Buffer.from(obj4Post),
  ]);

  const fmt = (n: number) => String(n).padStart(10, '0');
  const xrefTrailer = [
    'xref',
    '0 5',
    `0000000000 65535 f `,
    `${fmt(off1)} 00000 n `,
    `${fmt(off2)} 00000 n `,
    `${fmt(off3)} 00000 n `,
    `${fmt(off4)} 00000 n `,
    'trailer',
    '<< /Size 5 /Root 1 0 R >>',
    'startxref',
    String(body.length),
    '%%EOF',
    '',
  ].join('\n');

  return Buffer.concat([body, Buffer.from(xrefTrailer)]);
}

// ---------------------------------------------------------------------------
// File builder helpers
// ---------------------------------------------------------------------------

/**
 * Build N in-memory files for Playwright's `setInputFiles`.
 * PDF files contain valid structure (header, xref, trailer); non-PDF
 * extensions (used for unsupported-type rejection tests) use a plain buffer
 * since there is no meaningful "valid" format for arbitrary extensions.
 */
export function makeFiles(
  count: number,
  opts: { prefix?: string; mimeType?: string; ext?: string } = {},
): Array<{ name: string; mimeType: string; buffer: Buffer }> {
  const prefix = opts.prefix ?? 'ok';
  const mimeType = opts.mimeType ?? 'application/pdf';
  const ext = opts.ext ?? 'pdf';
  const pad = String(count).length;
  return Array.from({ length: count }, (_, i) => ({
    name: `${prefix}-${String(i).padStart(pad, '0')}.${ext}`,
    mimeType,
    buffer:
      ext === 'pdf'
        ? validPdfBuffer(256, `${prefix}-${i}`)
        : Buffer.from(`e2e ${prefix} ${i}`),
  }));
}

/**
 * Build N files with a specific byte size each.
 * Useful for testing size-aware batching where the cumulative file size
 * determines how files are grouped into upload batches.
 * PDF files contain valid structure; the content stream is padded to reach
 * the target size. Non-PDF extensions use a plain filled buffer.
 */
export function makeSizedFiles(
  count: number,
  sizeBytes: number,
  opts: { prefix?: string; mimeType?: string; ext?: string } = {},
): Array<{ name: string; mimeType: string; buffer: Buffer }> {
  const prefix = opts.prefix ?? 'sized';
  const mimeType = opts.mimeType ?? 'application/pdf';
  const ext = opts.ext ?? 'pdf';
  const pad = String(count).length;
  return Array.from({ length: count }, (_, i) => ({
    name: `${prefix}-${String(i).padStart(pad, '0')}.${ext}`,
    mimeType,
    buffer:
      ext === 'pdf'
        ? validPdfBuffer(sizeBytes, `${prefix}-${i}`)
        : Buffer.alloc(sizeBytes, `e2e ${prefix} ${i}`),
  }));
}

/**
 * Build a single PDF file whose total size exceeds `limitBytes`.
 * For limits < 50 MB this returns an in-memory buffer; for limits >= 50 MB
 * it falls back to {@link writeOversizeFiles} internally.
 *
 * **Prefer {@link writeOversizeFiles}** in new tests — it works at any limit
 * and avoids hitting Playwright's 50 MB `setInputFiles` buffer cap.
 */
export function makeOversizeFile(
  limitBytes: number,
): { name: string; mimeType: string; buffer: Buffer } {
  return {
    name: 'oversize.pdf',
    mimeType: 'application/pdf',
    buffer: validPdfBuffer(limitBytes + 1, 'oversize'),
  };
}

// ---------------------------------------------------------------------------
// Disk-based large-file helpers — avoid Playwright's 50 MB buffer cap
// ---------------------------------------------------------------------------

const PLAYWRIGHT_BUFFER_CAP = 50 * 1024 * 1024;

/**
 * Write one or more oversized PDF files to a temp directory and return
 * the file paths array (for `setInputFiles`) plus a cleanup function.
 *
 * Playwright's `setInputFiles` rejects in-memory buffers > 50 MB, so any
 * test that needs files larger than that must write them to disk first.
 *
 * @example
 *   const { paths, cleanup } = writeOversizeFiles(realMaxBytes, 2);
 *   try {
 *     await input.setInputFiles(paths);
 *     // … assertions …
 *   } finally { cleanup(); }
 */
export function writeOversizeFiles(
  limitBytes: number,
  count = 1,
  opts: { prefix?: string } = {},
): { paths: string[]; cleanup: () => void } {
  const prefix = opts.prefix ?? 'oversize';
  const dir = mkdtempSync(join(tmpdir(), 'e2e-oversize-'));
  const paths: string[] = [];
  for (let i = 0; i < count; i++) {
    const name = `${prefix}-${i}.pdf`;
    const filePath = join(dir, name);
    writeFileSync(filePath, validPdfBuffer(limitBytes + 1, `${prefix}-${i}`));
    paths.push(filePath);
  }
  return {
    paths,
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  };
}

/**
 * Write a single file of exactly `sizeBytes` to a temp directory.
 * Used for boundary tests (e.g. file at exactly the size limit).
 *
 * Uses a raw zero-filled buffer instead of `validPdfBuffer` because the
 * PDF helper's structural overhead makes the output slightly larger than
 * `sizeBytes` — the boundary test needs an exact byte count.
 */
export function writeExactSizeFile(
  sizeBytes: number,
  name = 'at-limit.pdf',
): { path: string; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), 'e2e-exact-'));
  const filePath = join(dir, name);
  writeFileSync(filePath, Buffer.alloc(sizeBytes));
  return {
    path: filePath,
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  };
}

/**
 * Returns true when `sizeBytes` is safe for Playwright's in-memory
 * `setInputFiles` (under the 50 MB cap). Callers can branch between
 * buffer and disk strategies accordingly.
 */
export function fitsInPlaywrightBuffer(sizeBytes: number): boolean {
  return sizeBytes < PLAYWRIGHT_BUFFER_CAP;
}

/**
 * Navigate to a KB page and open the upload sidebar.
 * Returns the file `<input>` locator, or null if the affordance is absent
 * (permission mismatch, KB not found, etc.) so callers can test.skip.
 *
 * WHY recordGroup: the sidebar navigates with nodeType=recordGroup for KB
 * roots (see @sidebar/knowledge-base/page.tsx:255).  The backend validates
 * parent_type and rejects `kb` with 400 — only recordGroup/record/app/folder
 * are accepted.
 *
 * WHY just waitFor the button (no waitForResponse):
 * The "New" button is inside <Header>, which only renders after this chain:
 *   (1) sidebar loads  →  collectionsNavigationReady flips true
 *   (2) fetchTableData fires (gated on step 1)
 *   (3) API responds with breadcrumbs  →  tableData.breadcrumbs truthy
 *   (4) React re-renders Header
 * Steps 1-4 run entirely as React effects after page.goto returns.
 * page.waitForResponse() inherits actionTimeout (30 s) as its deadline —
 * if the sidebar is slow the whole chain can exceed 30 s, the listener
 * times out before the breadcrumbs response arrives, and openUploadSidebar
 * returns null even though the button would have appeared moments later.
 * Waiting directly for the button with a generous timeout is simpler, avoids
 * that race, and is the correct end-to-end condition we actually need.
 */
/**
 * Fixed timeouts for openUploadSidebar so a slow/unavailable backend causes
 * a quick `test.skip` instead of hanging for the caller's full test timeout.
 * Without these, `waitFor` inherits the test timeout — tests that set
 * `test.setTimeout(180_000)` would block for up to 3 minutes waiting for a
 * button that will never appear when the backend is down.
 */
const SIDEBAR_OPEN_TIMEOUT_MS = 45_000;
const MENU_ITEM_TIMEOUT_MS = 15_000;

export async function openUploadSidebar(
  page: Page,
  kbId: string,
): Promise<Locator | null> {
  await page.goto(`/knowledge-base/?nodeType=recordGroup&nodeId=${kbId}`);

  // data-testid="new-dropdown-trigger" is set on the header's New dropdown
  // button (both desktop Button and mobile IconButton variants in header.tsx).
  // Using the testid avoids the strict-mode violation caused by
  // getByRole('button', { name: 'New' }) matching both the dropdown trigger
  // and sidebar KB-tree buttons whose accessible names also contain "New".
  const newButton = page.getByTestId('new-dropdown-trigger');
  const buttonVisible = await newButton
    .waitFor({ state: 'visible', timeout: SIDEBAR_OPEN_TIMEOUT_MS })
    .then(() => true)
    .catch(() => false);
  if (!buttonVisible) return null;
  await newButton.click();

  // Radix DropdownMenu.Content mounts in a portal with role="menu".
  // Wait directly for the "Upload Data" menuitem — it only appears once the
  // portal has mounted, so this implicitly handles the portal-mount race.
  // Accessible name is "file_upload Upload Data" (icon text prepended by
  // MaterialIcon); substring match (no exact:true) is required.
  const uploadItem = page.getByRole('menuitem', { name: 'Upload Data' });
  const itemVisible = await uploadItem
    .waitFor({ state: 'visible', timeout: MENU_ITEM_TIMEOUT_MS })
    .then(() => true)
    .catch(() => false);
  if (!itemVisible) return null;
  await uploadItem.click();

  const fileInput = page.getByTestId('upload-input-file');
  await fileInput.waitFor({ state: 'attached', timeout: MENU_ITEM_TIMEOUT_MS });
  return fileInput;
}
