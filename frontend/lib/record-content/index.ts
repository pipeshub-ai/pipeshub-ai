import { selectRecordPreviewCacheEnabled, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { getOrgIdFromToken, getUserIdFromToken } from '@/lib/utils/jwt';
import {
  createRecordContentCache,
  getRecordContentCache,
  type Lease,
  type Pending,
  type Variant,
} from './cache';
import { fetchRecordContent } from './fetch';
import type { Grant } from './grant';

export type { Lease, Pending, Variant } from './cache';
export { PartyLeftError, SessionEndedError } from './cache';
export type { Grant } from './grant';

/** The tab's cache. Created on first use so a test can import this module without a session. */
function cache() {
  try {
    return getRecordContentCache();
  } catch {
    return createRecordContentCache({
      fetcher: fetchRecordContent,
      // Default on: an unloaded or absent flag does not switch the cache off.
      enabled: () => selectRecordPreviewCacheEnabled(useFeatureFlagsStore.getState()),
    });
  }
}

/**
 * Opens a record's bytes. `grant` comes from `KnowledgeBaseApi.getRecordDetailsForOpen`, which every
 * open calls first; this function takes no details object, so a caller cannot make a reuse claim.
 */
export function openRecordContent(recordId: string, variant: Variant, grant?: Grant): Pending {
  return cache().acquire(recordId, variant, grant);
}

/** A refusal (403, 404, 409) from the details call or the stream: no variant of the record stays. */
export function evictRecordContent(recordId: string): void {
  if (getOrgIdFromToken() && getUserIdFromToken()) cache().evictRecord(recordId);
}

export function clearRecordContent(): void {
  cache().clear();
}

export type RecordLease = Lease;
