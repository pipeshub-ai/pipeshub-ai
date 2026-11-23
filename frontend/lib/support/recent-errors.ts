'use client';

import type { AxiosError } from 'axios';
import { apiClient } from '@/lib/api';
import { useAuthStore } from '@/lib/store/auth-store';

export interface RecentError {
  method: string;
  path: string;
  status: number;
  requestId?: string;
  at: string;
}

const MAX_ENTRIES = 5;
const MAX_AGE_MS = 30 * 60 * 1000;
const MAX_PATH_LENGTH = 200;
const RECORDED_METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']);
// Sign-in and token calls can carry credentials in their paths; support's own calls would loop.
const SKIPPED_PREFIXES = ['/api/v1/auth', '/api/v1/userAccount', '/api/v1/support', '/api/v1/feedback'];
const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{1,100}$/;

let entries: RecentError[] = [];
let installed = false;
// A call retried after a token refresh passes the recorder twice with the same error.
const seen = new WeakSet<object>();

function toAxiosError(error: unknown): AxiosError | null {
  const candidate = error as { isAxiosError?: boolean; originalError?: unknown } | null;
  if (candidate?.isAxiosError === true) return error as AxiosError;
  const original = candidate?.originalError as { isAxiosError?: boolean } | undefined;
  return original?.isAxiosError === true ? (original as AxiosError) : null;
}

function pathOnly(url: string): string | null {
  try {
    const { pathname } = new URL(url, 'http://placeholder.invalid');
    return pathname.startsWith('/') ? pathname.slice(0, MAX_PATH_LENGTH) : null;
  } catch {
    return null;
  }
}

function requestIdOf(error: AxiosError): string | undefined {
  const data = error.response?.data as { requestId?: unknown; error?: { requestId?: unknown } } | undefined;
  const fromBody = data?.error?.requestId ?? data?.requestId;
  const fromHeader = error.config?.headers?.['x-request-id'];
  const candidate = typeof fromBody === 'string' ? fromBody : fromHeader;
  return typeof candidate === 'string' && REQUEST_ID_PATTERN.test(candidate) ? candidate : undefined;
}

/**
 * Server and network failures always count. A 4xx counts only when the app showed its own error for it:
 * calls that handle a 4xx in place (an empty search answering 404) are expected outcomes, not problems.
 */
function isFailure(error: AxiosError, status: number): boolean {
  if (status === 0 || status === 408 || status === 429 || status >= 500) return true;
  const handledInPlace = (error.config as { suppressErrorToast?: unknown } | undefined)?.suppressErrorToast;
  return handledInPlace === undefined || handledInPlace === false;
}

/** Keeps a failed API call, minus anything after `?`, so support can find it by request ID. */
export function recordFailedRequest(error: unknown): void {
  const axiosError = toAxiosError(error);
  if (!axiosError?.config?.url || axiosError.code === 'ERR_CANCELED') return;
  if (seen.has(axiosError)) return;
  seen.add(axiosError);
  const status = axiosError.response?.status ?? 0;
  if (status === 401 || !isFailure(axiosError, status)) return;
  const method = (axiosError.config.method ?? 'get').toUpperCase();
  const path = pathOnly(axiosError.config.url);
  if (!RECORDED_METHODS.has(method) || !path) return;
  if (SKIPPED_PREFIXES.some((prefix) => path.startsWith(prefix))) return;

  entries = [...entries, { method, path, status, requestId: requestIdOf(axiosError), at: new Date().toISOString() }].slice(
    -MAX_ENTRIES,
  );
}

export function getRecentErrors(): RecentError[] {
  const cutoff = Date.now() - MAX_AGE_MS;
  entries = entries.filter((entry) => Date.parse(entry.at) >= cutoff);
  return [...entries];
}

export function clearRecentErrors(): void {
  entries = [];
}

/** Watches the API client for failed calls; forgets them on sign-out. Safe to call twice. */
export function installRecentErrorRecorder(): void {
  if (installed) return;
  installed = true;
  apiClient.interceptors.response.use(undefined, (error: unknown) => {
    recordFailedRequest(error);
    return Promise.reject(error);
  });
  useAuthStore.subscribe((state, previous) => {
    if (previous.accessToken && !state.accessToken) clearRecentErrors();
  });
}
