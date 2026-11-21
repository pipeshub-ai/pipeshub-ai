import axios, { type AxiosInstance } from 'axios';
import axiosRetry, { retryAfter } from 'axios-retry';

// A 429 whose Retry-After is longer than this goes straight back to the caller
// with its Retry-After: waiting it out would hold the request open (a preview
// would hang for the length of a source's rate limit).
export const MAX_RETRY_AFTER_MS = 5000;

const installed = new WeakSet<AxiosInstance>();

/**
 * The retry policy of connector-service calls, on the process-wide axios client
 * by default. Installing it again on the same client is a no-op: every router
 * that relies on the policy calls this, and a second set of interceptors would
 * retry on its own terms (a 429's long Retry-After would be waited out).
 */
export function installConnectorRetryPolicy(
  client: AxiosInstance = axios,
): void {
  if (installed.has(client)) return;
  installed.add(client);
  axiosRetry(client, {
    retries: 3,
    retryDelay: (retryCount, error) =>
      axiosRetry.exponentialDelay(retryCount, error),
    retryCondition: (error) => {
      if (
        error.response?.status === 429 &&
        retryAfter(error) > MAX_RETRY_AFTER_MS
      ) {
        return false;
      }
      return !!(
        axiosRetry.isNetworkOrIdempotentRequestError(error) ||
        (error.response && error.response.status >= 500)
      );
    },
  });
}
