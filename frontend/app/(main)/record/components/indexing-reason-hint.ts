import { isRateLimitPause } from '@/lib/utils/rate-limit-pause';

const REASON_STATUSES = new Set([
  'FAILED',
  'AUTO_INDEX_OFF',
  'FILE_TYPE_NOT_SUPPORTED',
  'PASSWORD_PROTECTED',
  'NOT_STARTED',
]);

/**
 * How a record's `reason` is shown next to its indexing status: as the reason it is not indexed,
 * as a note on a completed record (for example, that only part of a large file is indexed), or not
 * at all.
 */
export function indexingReasonHint(
  status: string | undefined,
  reason: string | undefined,
  retryNotBefore?: number | null,
): 'reason' | 'note' | null {
  if (!reason?.trim()) return null;
  if (isRateLimitPause(status, reason, retryNotBefore) || (status && REASON_STATUSES.has(status))) {
    return 'reason';
  }
  return status === 'COMPLETED' ? 'note' : null;
}
