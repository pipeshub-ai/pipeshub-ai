// Single source of truth (TS side) for mapping each indexingStatus value to a
// display bucket. MUST stay in lockstep with the Python side
// (app/services/progress/status_classification.py). QUEUED/IN_PROGRESS/
// NOT_STARTED hold the bar open; everything else is settled. AUTO_INDEX_OFF and
// PAUSED are deliberately "skipped" (settled), not pending — otherwise the bar
// would never hide.

export type ProgressBucket = 'pending' | 'done' | 'failed' | 'skipped';

export const BUCKET_BY_STATUS: Record<string, ProgressBucket> = {
  QUEUED: 'pending',
  IN_PROGRESS: 'pending',
  NOT_STARTED: 'pending',
  COMPLETED: 'done',
  EMPTY: 'done',
  FAILED: 'failed',
  FILE_TYPE_NOT_SUPPORTED: 'skipped',
  AUTO_INDEX_OFF: 'skipped',
  ENABLE_MULTIMODAL_MODELS: 'skipped',
  PAUSED: 'skipped',
};

export function classify(status: string): ProgressBucket | undefined {
  return BUCKET_BY_STATUS[status];
}
