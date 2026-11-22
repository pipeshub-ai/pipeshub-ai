import axios from 'axios';
import axiosRetry, {
  exponentialDelay,
  IAxiosRetryConfig,
  retryAfter,
} from 'axios-retry';

// A 429 whose Retry-After is longer than this goes straight back to the caller
// with its Retry-After: waiting it out would hold the request open (a preview
// would hang for the length of a source's rate limit).
const MAX_RETRY_AFTER_MS = 5000;

/**
 * The default axios instance's retries (installed in index.ts): reads only
 * (GET, HEAD, OPTIONS), so a write the upstream may already have acted on is
 * never sent twice, and never a call we cancelled. PUT and DELETE are left out
 * too: a replayed skill update writes a second revision, and a replayed delete
 * answers 404 for a delete that worked.
 */
export const RETRY_POLICY: IAxiosRetryConfig = {
  retries: 3,
  retryDelay: exponentialDelay,
  retryCondition: (error) =>
    !axios.isCancel(error) &&
    !(
      error.response?.status === 429 && retryAfter(error) > MAX_RETRY_AFTER_MS
    ) &&
    axiosRetry.isSafeRequestError(error),
};
