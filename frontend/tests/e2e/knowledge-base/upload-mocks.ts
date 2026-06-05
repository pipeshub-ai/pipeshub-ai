import type { Page, Route } from '@playwright/test';

/**
 * Network mocks for the Collections file-upload e2e suite.
 *
 * The knowledge-base page validates files SERVER-SIDE: the upload endpoint
 * streams a per-file outcome (`file:succeeded` / `file:failed` with a stable
 * reason code) and a final `done`. By intercepting that endpoint we can drive
 * every outcome — accepted, oversize, unsupported type, duplicate name, server
 * error — deterministically, with tiny in-memory files and NO real backend or
 * object storage (so even a 2000-file batch is fast and reproducible).
 *
 * We also mock the KB-context reads (upload limits + knowledge-hub nodes) so the
 * page renders a single OWNED collection. Ownership makes
 * `tableData.permissions.canCreateFolders` truthy, which is what gates the
 * header's "New > Upload" affordance.
 */

export const TEST_KB_ID = 'e2e-kb-0001';
export const TEST_KB_NAME = 'E2E Upload Collection';
export const DEFAULT_MAX_FILE_SIZE_BYTES = 30 * 1024 * 1024;

/** Reason codes the backend emits on `file:failed` (see fp.constant.ts). */
export const REJECTION = {
  EXCEEDS_SIZE_LIMIT: 'EXCEEDS_SIZE_LIMIT',
  UNSUPPORTED_TYPE: 'UNSUPPORTED_TYPE',
  DUPLICATE_NAME: 'DUPLICATE_NAME',
} as const;

export type UploadOutcome =
  | { outcome: 'succeeded' }
  | { outcome: 'failed'; reason?: string; errors?: string[] };

function jsonFulfil(route: Route, body: unknown): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

/** A single SSE frame: `event: <name>\ndata: <json>\n\n`. */
function sseFrame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const ownerPermissions = {
  role: 'OWNER',
  canUpload: true,
  canCreateFolders: true,
  canEdit: true,
  canDelete: true,
  canManagePermissions: true,
};

const kbNode = (kbId: string) => ({
  id: kbId,
  name: TEST_KB_NAME,
  nodeType: 'kb',
  parentId: null,
  origin: 'COLLECTION',
  hasChildren: false,
  permission: { role: 'OWNER', canEdit: true, canDelete: true },
  sharingStatus: 'private',
});

/** Root listing (sidebar init): one owned collection. */
function rootCollectionsResponse(kbId: string) {
  return {
    success: true,
    error: null,
    id: null,
    currentNode: null,
    parentNode: null,
    items: [kbNode(kbId)],
    pagination: { page: 1, limit: 50, totalItems: 1, totalPages: 1, hasNext: false, hasPrev: false },
    breadcrumbs: [],
    counts: { items: [], total: 0 },
    permissions: null,
  };
}

/** Selected-collection view with OWNER permissions (empty, so it's a clean target). */
function writableNodeResponse(kbId: string) {
  return {
    success: true,
    error: null,
    id: kbId,
    currentNode: {
      id: kbId,
      name: TEST_KB_NAME,
      nodeType: 'kb',
      origin: 'COLLECTION',
      createdAt: 1_600_000_000_000,
      updatedAt: 1_600_000_000_000,
    },
    parentNode: null,
    items: [],
    pagination: { page: 1, limit: 50, totalItems: 0, totalPages: 1, hasNext: false, hasPrev: false },
    breadcrumbs: [
      { id: 'app-root', name: 'Collections', nodeType: 'app' },
      { id: kbId, name: TEST_KB_NAME, nodeType: 'kb' },
    ],
    counts: { items: [], total: 0 },
    permissions: ownerPermissions,
  };
}

/**
 * Mock the reads that render a writable collection. Call BEFORE navigating.
 * Routes registered here are intentionally less specific than {@link mockUpload}
 * (Playwright matches most-recently-added first, so register the upload mock
 * after this one).
 */
export async function mockKbContext(
  page: Page,
  opts: { kbId?: string; maxFileSizeBytes?: number } = {},
): Promise<void> {
  const kbId = opts.kbId ?? TEST_KB_ID;
  const maxFileSizeBytes = opts.maxFileSizeBytes ?? DEFAULT_MAX_FILE_SIZE_BYTES;

  await page.route('**/api/v1/knowledge*base/limits**', (route) =>
    jsonFulfil(route, { maxFileSizeBytes }),
  );

  await page.route('**/knowledge-hub/nodes**', (route) => {
    const path = new URL(route.request().url()).pathname;
    // A selected node is `.../knowledge-hub/nodes/<type>/<id>`; the bare
    // `.../knowledge-hub/nodes` is the sidebar/root listing.
    return /\/knowledge-hub\/nodes\/[^/]+\/[^/]+$/.test(path)
      ? jsonFulfil(route, writableNodeResponse(kbId))
      : jsonFulfil(route, rootCollectionsResponse(kbId));
  });
}

/** Basename of a (possibly nested) upload path: "docs/sub/a.pdf" -> "a.pdf". */
function basename(p: string): string {
  const parts = p.split('/');
  return parts[parts.length - 1] || p;
}

/**
 * The per-batch `file_path` list the client sends. The page always attaches a
 * `files_metadata` field (`[{file_path, last_modified}, ...]`); `file_path` is
 * the flat name for file uploads and the relative path for folder uploads, and
 * is exactly what the client matches rows on. Falls back to the multipart
 * `filename="..."` headers if metadata is absent.
 */
function batchPaths(post: string): string[] {
  const meta = post.match(/name="files_metadata"\r?\n\r?\n([\s\S]*?)\r?\n--/);
  if (meta) {
    try {
      const parsed = JSON.parse(meta[1].trim()) as Array<{ file_path?: string }>;
      const paths = parsed.map((m) => m?.file_path).filter((p): p is string => !!p);
      if (paths.length) return paths;
    } catch {
      /* fall through to filename parsing */
    }
  }
  return [...post.matchAll(/name="files";\s*filename="([^"]*)"/g)]
    .map((m) => m[1])
    .filter(Boolean);
}

/** Build the SSE response body for one batch POST (keyed by the sent paths). */
function buildUploadSSE(post: string, decide: (fileName: string) => UploadOutcome): string {
  const paths = batchPaths(post);
  let succeeded = 0;
  let failed = 0;
  let body = ': connected\n\n';
  for (const filePath of paths) {
    const d = decide(basename(filePath));
    if (d.outcome === 'succeeded') {
      succeeded += 1;
      body += sseFrame('file:succeeded', {
        fileName: basename(filePath),
        filePath,
        recordId: `rec-${filePath}`,
      });
    } else {
      failed += 1;
      body += sseFrame('file:failed', {
        fileName: basename(filePath),
        filePath,
        stage: 'index',
        reason: d.reason,
        errors: d.errors,
      });
    }
  }
  body += sseFrame('done', { summary: { total: paths.length, succeeded, failed } });
  return body;
}

function fulfilSSE(route: Route, body: string): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'text/event-stream',
    headers: { 'cache-control': 'no-cache' },
    body,
  });
}

/**
 * Mock the upload endpoint. For each intercepted batch POST, the per-file paths
 * are read from the multipart body and `decide(basename)` chooses each file's
 * outcome; the crafted SSE response (keyed by the SAME path the client sent, so
 * rows resolve) is streamed back. Handles the page's batching (10 files/batch)
 * and folder uploads transparently — every batch request is answered
 * independently.
 */
export async function mockUpload(
  page: Page,
  decide: (fileName: string) => UploadOutcome,
): Promise<void> {
  await page.route('**/api/v1/knowledge*base/**/upload', (route: Route) =>
    fulfilSSE(route, buildUploadSSE(route.request().postData() ?? '', decide)),
  );
}

/** The exact 429 body the Node rate limiter returns (see rate-limit.middleware.ts). */
export const RATE_LIMIT_BODY = {
  error: {
    code: 'HTTP_TOO_MANY_REQUESTS',
    message: 'Too many requests. Please try again later.',
    retryAfter: 1,
  },
};

/**
 * Mock the upload endpoint to return 429 with the exact rate-limiter body, so a
 * test can assert the client surfaces the server's real message.
 */
export async function mockUploadRateLimited(page: Page): Promise<void> {
  await page.route('**/api/v1/knowledge*base/**/upload', (route: Route) =>
    route.fulfill({
      status: 429,
      contentType: 'application/json',
      headers: { 'retry-after': String(RATE_LIMIT_BODY.error.retryAfter) },
      body: JSON.stringify(RATE_LIMIT_BODY),
    }),
  );
}

/** Mock the upload endpoint to hard-fail the whole batch (transport error). */
export async function mockUploadServerError(page: Page, status = 500): Promise<void> {
  await page.route('**/api/v1/knowledge*base/**/upload', (route) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ error: 'boom' }) }),
  );
}

/** Build N in-memory files with a given name prefix for setInputFiles. */
export function makeFiles(
  count: number,
  opts: { prefix?: string; mimeType?: string; ext?: string; bytes?: number } = {},
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
 * Navigate to the test collection (fully mocked) and open the Upload sidebar.
 * Returns the file `<input>` locator, or null if the upload affordance never
 * appeared (so callers can skip rather than hang) — e.g. if the mocked response
 * shape drifts from the app's expectations.
 */
export async function openUploadSidebar(page: Page, kbId: string = TEST_KB_ID) {
  await page.goto(`/knowledge-base/?nodeType=kb&nodeId=${kbId}`);

  const newButton = page.getByRole('button', { name: /new/i }).first();
  if (!(await newButton.isVisible({ timeout: 15_000 }).catch(() => false))) {
    return null;
  }
  await newButton.click();

  const uploadItem = page.getByRole('menuitem', { name: /upload/i }).first();
  if (!(await uploadItem.isVisible({ timeout: 5_000 }).catch(() => false))) {
    return null;
  }
  await uploadItem.click();

  const fileInput = page.getByTestId('upload-input-file');
  return (await fileInput.count()) ? fileInput : null;
}
