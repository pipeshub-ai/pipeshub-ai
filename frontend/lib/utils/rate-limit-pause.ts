// Written by the backend's `rate_limited_will_retry` (backend/python/app/utils/user_errors.py).
const RATE_LIMIT_PAUSE_WORDING = 'is limiting how often PipesHub can read from it';

/**
 * Whether a record's `reason` should be shown although it is QUEUED: its
 * source rate-limited the content fetch, and the reason says when indexing
 * resumes. Every other QUEUED reason stays hidden, such as the generic "will
 * retry" of a transient requeue.
 *
 * The record details carry `retryNotBefore`; the Knowledge Hub list does not,
 * so there the reason's wording is the signal.
 */
export function isRateLimitPause(
  status: string | null | undefined,
  reason: string | null | undefined,
  retryNotBefore?: number | null,
): boolean {
  if (status !== 'QUEUED' || !reason?.trim()) return false;
  return retryNotBefore != null || reason.includes(RATE_LIMIT_PAUSE_WORDING);
}
