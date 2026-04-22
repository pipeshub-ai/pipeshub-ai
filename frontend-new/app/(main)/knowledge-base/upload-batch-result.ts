import { i18n } from '@/lib/i18n';

/**
 * Maps KB multipart upload HTTP responses to per-file outcomes.
 *
 * When some files fail placeholder validation, `records` only includes successes and is
 * shorter than the client batch. `records` order matches the original multipart order
 * with failed rows removed — so we pair successes by index after resolving failures.
 */

export type UploadBatchEntry = {
  storeId: string;
  file: File;
  filePath: string;
};

export type FailedFileDetail = {
  fileName?: string;
  filePath?: string;
  error?: string;
};

function asRecord(obj: unknown): Record<string, unknown> | null {
  return obj && typeof obj === 'object' && !Array.isArray(obj) ? (obj as Record<string, unknown>) : null;
}

/** Lowercase, trim, and slash-normalize for path/name comparisons. */
function normalizePath(s: string): string {
  return s.trim().toLowerCase().replace(/\\/g, '/');
}

function basename(path: string): string {
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] ?? path;
}

/** Client `file_path` vs server `failedFilesDetails.filePath` (prefix / segment tail). */
function uploadPathsReferToSameFile(clientPath: string, serverPath: string): boolean {
  const c = normalizePath(clientPath);
  const s = normalizePath(serverPath);
  if (!c || !s) return false;
  if (c === s) return true;
  if (c.endsWith('/' + s) || s.endsWith('/' + c)) return true;
  const cParts = c.split('/').filter(Boolean);
  const sParts = s.split('/').filter(Boolean);
  if (cParts.length >= sParts.length) {
    const tail = cParts.slice(-sParts.length).join('/');
    if (tail === sParts.join('/')) return true;
  }
  if (sParts.length >= cParts.length) {
    const tail = sParts.slice(-cParts.length).join('/');
    if (tail === cParts.join('/')) return true;
  }
  return false;
}

function normalizeKnowledgeBaseUploadBody(body: unknown): {
  records: Record<string, unknown>[];
  failedDetails: FailedFileDetail[];
  successfulFiles?: number;
  failedCount?: number;
} {
  const root = asRecord(body) ?? {};
  // Single unwrap of `body.data` only — not `data.data` (backend does not double-wrap today).
  const nested = asRecord(root.data);

  let recordsRaw: unknown = root.records ?? nested?.records;
  if (!Array.isArray(recordsRaw)) recordsRaw = [];

  let failedDetails: unknown = root.failedFilesDetails ?? nested?.failedFilesDetails;
  if (!Array.isArray(failedDetails)) {
    const ff = root.failedFiles ?? nested?.failedFiles;
    if (Array.isArray(ff) && ff.length > 0 && typeof ff[0] === 'object') {
      failedDetails = ff;
    } else {
      failedDetails = [];
    }
  }

  const successfulFiles =
    typeof root.successfulFiles === 'number'
      ? root.successfulFiles
      : typeof nested?.successfulFiles === 'number'
        ? nested.successfulFiles
        : undefined;

  const failedCount =
    typeof root.failedFiles === 'number'
      ? root.failedFiles
      : typeof nested?.failedFiles === 'number'
        ? nested.failedFiles
        : undefined;

  return {
    records: recordsRaw as Record<string, unknown>[],
    failedDetails: failedDetails as FailedFileDetail[],
    successfulFiles,
    failedCount,
  };
}

function recordDisplayName(rec: Record<string, unknown>): string | undefined {
  const direct = rec.recordName;
  if (typeof direct === 'string' && direct.trim()) return direct.trim();

  const fr = asRecord(rec.fileRecord);
  const n = fr?.name;
  if (typeof n === 'string' && n.trim()) return n.trim();

  return undefined;
}

function recordPathHint(rec: Record<string, unknown>): string | undefined {
  const p = rec.filePath ?? rec.file_path;
  return typeof p === 'string' && p.length > 0 ? p : undefined;
}

/**
 * Assign each failedFilesDetails row to at most one batch row (paths may differ by prefix).
 */
function matchFailedDetailsToStoreIds(batch: UploadBatchEntry[], failedDetails: FailedFileDetail[]): Map<string, string> {
  const storeIdToMessage = new Map<string, string>();
  const used = new Set<string>();

  for (const ff of failedDetails) {
    let entry: UploadBatchEntry | undefined;

    if (ff.filePath != null && ff.filePath !== '') {
      const pathMatches = batch.filter(
        (e) => !used.has(e.storeId) && uploadPathsReferToSameFile(e.filePath, ff.filePath!)
      );
      if (pathMatches.length === 1) entry = pathMatches[0];
    }

    if (!entry && ff.fileName) {
      const nn = normalizePath(ff.fileName);
      const byName = batch.filter(
        (e) =>
          !used.has(e.storeId) &&
          (normalizePath(e.file.name) === nn || normalizePath(basename(e.filePath)) === nn)
      );
      if (byName.length === 1) entry = byName[0];
    }

    if (entry) {
      used.add(entry.storeId);
      storeIdToMessage.set(entry.storeId, ff.error || i18n.t('uploadProgress.uploadFailedDefault'));
    }
  }

  return storeIdToMessage;
}

function greedyMatchRecordsToEntries(
  records: Record<string, unknown>[],
  candidates: UploadBatchEntry[],
  markComplete: (storeId: string) => void,
  consumed: Set<string>
): void {
  const findEntryForRecord = (rec: Record<string, unknown>): UploadBatchEntry | undefined => {
    const pathHint = recordPathHint(rec);
    if (pathHint) {
      const hits = candidates.filter(
        (e) => !consumed.has(e.storeId) && uploadPathsReferToSameFile(e.filePath, pathHint)
      );
      if (hits.length === 1) return hits[0];
    }

    const name = recordDisplayName(rec);
    if (name) {
      const nn = normalizePath(name);
      const byFileName = candidates.filter(
        (e) => !consumed.has(e.storeId) && normalizePath(e.file.name) === nn
      );
      if (byFileName.length === 1) return byFileName[0];

      const byPathTail = candidates.filter((e) => {
        if (consumed.has(e.storeId)) return false;
        return normalizePath(basename(e.filePath)) === nn;
      });
      if (byPathTail.length === 1) return byPathTail[0];
    }

    return undefined;
  };

  for (const rec of records) {
    const entry = findEntryForRecord(rec);
    if (entry) {
      consumed.add(entry.storeId);
      markComplete(entry.storeId);
    }
  }
}

/**
 * Applies upload HTTP response to a single client batch: resolves failures from
 * `failedFilesDetails`, completes successes (prefer index order on the success subset),
 * then fails leftovers only when they cannot be accounted for.
 */
export function applyKnowledgeBaseUploadBatchResult(
  batch: UploadBatchEntry[],
  body: unknown,
  complete: (storeId: string) => void,
  fail: (storeId: string, message: string) => void
): void {
  const { records, failedDetails, successfulFiles, failedCount } = normalizeKnowledgeBaseUploadBody(body);

  const failedCountSafe = failedCount ?? 0;
  const completed = new Set<string>();

  const markComplete = (storeId: string) => {
    if (completed.has(storeId)) return;
    complete(storeId);
    completed.add(storeId);
  };

  const pendingFail = matchFailedDetailsToStoreIds(batch, failedDetails);

  const successOrdered = batch.filter((e) => !pendingFail.has(e.storeId));

  // 1) Complete successes: `records` is in the same order as multipart `files` with failed rows removed.
  if (Array.isArray(records) && records.length > 0) {
    if (records.length === successOrdered.length) {
      successOrdered.forEach((e, idx) => {
        if (records[idx]) markComplete(e.storeId);
      });
    } else {
      const consumed = new Set<string>();
      greedyMatchRecordsToEntries(records, successOrdered, markComplete, consumed);
    }

    // Full batch success (no rows in pendingFail, all records accounted for)
    const serverSaysFullSuccess =
      typeof successfulFiles === 'number' &&
      successfulFiles === batch.length &&
      failedCountSafe === 0 &&
      pendingFail.size === 0;

    const legacyFullSuccessNoCounts =
      typeof successfulFiles !== 'number' &&
      failedCountSafe === 0 &&
      failedDetails.length === 0 &&
      records.length === batch.length;

    if (serverSaysFullSuccess || legacyFullSuccessNoCounts) {
      batch.forEach((e) => {
        if (!completed.has(e.storeId)) markComplete(e.storeId);
      });
    }
  } else {
    const explicitOk =
      typeof successfulFiles === 'number' &&
      successfulFiles === batch.length &&
      failedCountSafe === 0 &&
      failedDetails.length === 0;

    if (explicitOk) {
      batch.forEach((e) => markComplete(e.storeId));
    }
  }

  // 2) Explicit failures (rows were excluded from successOrdered; never marked completed here)
  pendingFail.forEach((message, storeId) => {
    fail(storeId, message);
  });

  // 3) Rows still unresolved after matching
  batch.forEach((e) => {
    if (completed.has(e.storeId)) return;
    if (pendingFail.has(e.storeId)) return;
    fail(e.storeId, i18n.t('uploadProgress.uploadIncomplete'));
  });
}
