import type { APIRequestContext, Locator, Page } from '@playwright/test';
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

/**
 * Build N tiny in-memory files for Playwright's `setInputFiles`.
 * Content is insignificant — all validation is server-side.
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
    buffer: Buffer.from(`e2e ${prefix} ${i}`),
  }));
}

/**
 * Build a single file whose buffer is one byte over `limitBytes`.
 * Uploading this triggers the backend's EXCEEDS_SIZE_LIMIT rejection.
 */
export function makeOversizeFile(
  limitBytes: number,
): { name: string; mimeType: string; buffer: Buffer } {
  return {
    name: 'oversize.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.alloc(limitBytes + 1),
  };
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
    .waitFor({ state: 'visible' })
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
    .waitFor({ state: 'visible' })
    .then(() => true)
    .catch(() => false);
  if (!itemVisible) return null;
  await uploadItem.click();

  const fileInput = page.getByTestId('upload-input-file');
  await fileInput.waitFor({ state: 'attached' });
  return fileInput;
}
