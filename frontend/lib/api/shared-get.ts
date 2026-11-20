import { apiClient } from './axios-instance';

const pending = new Map<string, Promise<unknown>>();

/**
 * A GET that several parts of the app make at the same moment (the org, read by the org profile and by the rail's
 * footer as the app starts): calls that overlap share one request. A call after it has answered asks again.
 */
export function sharedGet<T>(url: string): Promise<T> {
  const inFlight = pending.get(url) as Promise<T> | undefined;
  if (inFlight) return inFlight;
  const request = apiClient
    .get<T>(url)
    .then(({ data }) => data)
    .finally(() => pending.delete(url));
  pending.set(url, request);
  return request;
}
