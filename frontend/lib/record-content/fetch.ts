import { apiClient } from '@/lib/api/axios-instance';
import type { FetchArgs, FetchResult } from './cache';

const STREAM_PATH = '/api/v1/knowledgeBase/stream/record';

/**
 * The cache's transport. It stays on `apiClient`, so the Electron base URL, the stale-401 guard,
 * the proactive refresh, request ids and the timeout all apply. A 304 is a success status here:
 * it brings no body and no toast.
 */
export async function fetchRecordContent(args: FetchArgs): Promise<FetchResult> {
  const { recordId, variant, ifNoneMatch, signal, onProgress, suppressErrorToast, wantsValidator } = args;
  const response = await apiClient.get<Blob>(`${STREAM_PATH}/${encodeURIComponent(recordId)}`, {
    responseType: 'blob',
    params: variant === 'pdf' ? { convertTo: 'application/pdf' } : {},
    // The server computes a validator only for a request that asks for one.
    headers: {
      ...(ifNoneMatch ? { 'If-None-Match': ifNoneMatch } : {}),
      ...(wantsValidator ? { 'X-Record-Content-Open': '1' } : {}),
    },
    signal,
    suppressErrorToast,
    validateStatus: (s) => (s >= 200 && s < 300) || s === 304,
    onDownloadProgress: (event) => {
      const xhr = (event.event as { target?: XMLHttpRequest } | undefined)?.target;
      onProgress({
        loaded: event.loaded,
        status: xhr?.status ?? 200,
        etag: xhr?.getResponseHeader?.('ETag') ?? null,
      });
    },
  });
  if (response.status === 304) return { status: 304 };
  const blob = response.data;
  const etag = (response.headers?.etag as string | undefined) ?? null;
  return { status: 200, blob, etag, mime: blob.type || (response.headers?.['content-type'] as string) || '' };
}
