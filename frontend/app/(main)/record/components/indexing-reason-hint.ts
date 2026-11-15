import { isRateLimitPause } from '@/lib/utils/rate-limit-pause';

const REASON_STATUSES = new Set([
  'FAILED',
  'AUTO_INDEX_OFF',
  'FILE_TYPE_NOT_SUPPORTED',
  'PASSWORD_PROTECTED',
  'NOT_STARTED',
]);

export interface IndexingStatusHint {
  kind: 'reason' | 'note';
  content: string;
}

/**
 * What the info icon next to a record's indexing status shows: the `reason` it is not indexed, or,
 * on a COMPLETED record, its `indexingNote` (for example, that only part of a large file is
 * indexed). A COMPLETED record's `reason` belongs to a later stage (a skipped classification) and
 * is not shown here.
 */
export function indexingStatusHint(record: {
  indexingStatus?: string;
  reason?: string;
  indexingNote?: string | null;
  retryNotBefore?: number | null;
}): IndexingStatusHint | null {
  const { indexingStatus: status, reason, indexingNote, retryNotBefore } = record;
  if (
    reason?.trim() &&
    (isRateLimitPause(status, reason, retryNotBefore) || (status !== undefined && REASON_STATUSES.has(status)))
  ) {
    return { kind: 'reason', content: reason };
  }
  if (status === 'COMPLETED' && indexingNote?.trim()) {
    return { kind: 'note', content: indexingNote };
  }
  return null;
}
