/**
 * Streaming API module using native fetch
 *
 * WHY NOT AXIOS?
 * ==============
 * Axios does not support Server-Sent Events (SSE) or streaming responses.
 * Axios is built on XMLHttpRequest (in browsers) which buffers the entire
 * response before making it available. This means:
 *
 * 1. No access to response.body.getReader() - Axios doesn't expose the
 *    ReadableStream interface needed to read chunks as they arrive
 *
 * 2. No incremental processing - With axios, you must wait for the complete
 *    response, defeating the purpose of streaming for real-time chat
 *
 * 3. Memory issues - For long streams (like AI chat responses), buffering
 *    the entire response wastes memory and delays the first visible token
 *
 * Native fetch() with ReadableStream API allows us to:
 * - Process chunks immediately as they arrive from the server
 * - Display tokens to the user in real-time (better UX)
 * - Cancel streams mid-flight with AbortController
 * - Handle backpressure properly
 *
 * Note: We still use axios for regular API calls (see axios-instance.ts)
 * where its interceptors, error handling, and request/response transforms
 * are valuable.
 */

import { useAuthStore, logoutAndRedirect } from '@/lib/store/auth-store';
import {
  isTokenExpired,
  isRefreshInProgress,
  refreshAccessToken,
} from './token-refresh';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { streamingFetch, isElectron } from '@/lib/electron';
import { generateRequestId } from '@/lib/utils/request-id';

// Default to '' (same origin) rather than `undefined`, because template-string
// concatenation like `${API_BASE_URL}${url}` would otherwise stringify
// `undefined` and produce URLs like `/chat/undefined/api/v1/...`. In our
// standard deployment the Next.js static export is served by the Node.js
// backend, so the API is always same-origin and an empty prefix is correct.
// Override with `NEXT_PUBLIC_API_BASE_URL` at build time for split deployments.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

const SESSION_EXPIRED_MESSAGE = 'Session expired, please login again';

/**
 * Error thrown when an upload fails at the HTTP layer — i.e. the request is
 * rejected BEFORE the SSE stream starts (e.g. a 429 from the rate limiter, a
 * 413 payload-too-large, or a 5xx). Carries the server's structured fields so
 * the UI can show the EXACT reason instead of a generic "upload failed" /
 * "timed out", and so callers can honour `Retry-After` on a 429.
 */
export class UploadHttpError extends Error {
  readonly status: number;
  readonly code?: string;
  /** Seconds to wait before retrying, when the server provided it. */
  readonly retryAfter?: number;

  constructor(opts: {
    status: number;
    message: string;
    code?: string;
    retryAfter?: number;
  }) {
    super(opts.message);
    this.name = 'UploadHttpError';
    this.status = opts.status;
    this.code = opts.code;
    this.retryAfter = opts.retryAfter;
  }
}

/**
 * Build a precise error from a non-OK upload response by reading the JSON error
 * body the API returns (`{ error: { code, message, retryAfter } }`). This is why
 * the user sees the real reason — "Too many requests. Please try again later."
 * — rather than a bare status code or a misleading timeout. Falls back to the
 * `Retry-After` header, then to the status text.
 */
async function readUploadHttpError(response: Response): Promise<UploadHttpError> {
  let message = '';
  let code: string | undefined;
  let retryAfter: number | undefined;
  try {
    const body = await response.clone().json();
    const err = body?.error ?? body;
    if (err && typeof err === 'object') {
      if (typeof err.message === 'string') message = err.message;
      if (typeof err.code === 'string') code = err.code;
      if (typeof err.retryAfter === 'number') retryAfter = err.retryAfter;
    }
  } catch {
    /* non-JSON body — fall back below */
  }
  if (retryAfter == null) {
    const header = response.headers.get('retry-after');
    const parsed = header ? parseInt(header, 10) : NaN;
    if (Number.isFinite(parsed)) retryAfter = parsed;
  }
  if (!message) {
    message =
      response.status === 429
        ? 'Too many requests. Please try again later.'
        : `Upload failed (${response.status} ${response.statusText || 'error'})`;
  }
  return new UploadHttpError({ status: response.status, message, code, retryAfter });
}

/**
 * Resolve a fresh access token before issuing a stream.
 *
 * Native `fetch` bypasses the axios request interceptor, so this is the
 * one place where stream callers coordinate with the proactive refresh
 * scheduler:
 * - if the token is past the 90 s buffer, refresh first
 * - if a refresh is already in flight (timer or 401 retry), await the
 *   shared promise so we don't send the about-to-be-rotated token
 *
 * Returns the token (possibly refreshed) or, on refresh failure,
 * `{ sessionExpired: true }` after `logoutAndRedirect()` has been
 * triggered. Caller should `onError` and bail.
 */
async function ensureFreshToken(): Promise<
  { token: string | null; sessionExpired: false } | { token: null; sessionExpired: true }
> {
  let token = useAuthStore.getState().accessToken;
  if (!token) {
    return { token: null, sessionExpired: false };
  }

  if (isRefreshInProgress() || isTokenExpired(token)) {
    const ok = await refreshAccessToken();
    if (!ok) {
      logoutAndRedirect();
      return { token: null, sessionExpired: true };
    }
    token = useAuthStore.getState().accessToken;
  }

  return { token, sessionExpired: false };
}

export interface StreamingOptions {
  onChunk: (chunk: string) => void;
  onComplete: () => void;
  onError: (error: Error) => void;
  signal?: AbortSignal;
}

/**
 * Make a streaming request using native fetch
 *
 * @param url - API endpoint path (relative to base URL)
 * @param body - Request body to send
 * @param options - Streaming callbacks and options
 */
export async function streamRequest(
  url: string,
  body: Record<string, unknown>,
  options: StreamingOptions
): Promise<void> {
  const { onChunk, onComplete, onError, signal } = options;

  try {
    const { token, sessionExpired } = await ensureFreshToken();
    if (sessionExpired) {
      onError(new Error(SESSION_EXPIRED_MESSAGE));
      return;
    }

    const requestInit: RequestInit = {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-request-id': generateRequestId(),
        ...(token && { Authorization: `Bearer ${token}` }),
      },
      body: JSON.stringify(body),
      signal,
    };

    const response = isElectron()
      ? await streamingFetch(`${getApiBaseUrl()}${url}`, requestInit)
      : await fetch(`${API_BASE_URL}${url}`, requestInit);

    if (!response.ok) {
      throw new Error(`Stream request failed: ${response.status} ${response.statusText}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('No response body available for streaming');
    }

    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      onChunk(chunk);
    }

    onComplete();
  } catch (error) {
    // Don't report abort errors as actual errors
    if (error instanceof Error && error.name === 'AbortError') {
      return;
    }
    onError(error instanceof Error ? error : new Error('Stream request failed'));
  }
}

/**
 * Create an abort controller for cancelling streaming requests
 */
export function createStreamController(): {
  controller: AbortController;
  signal: AbortSignal;
  abort: () => void;
} {
  const controller = new AbortController();
  return {
    controller,
    signal: controller.signal,
    abort: () => controller.abort(),
  };
}

/**
 * SSE Event structure
 */
export interface SSEEvent<T = unknown> {
  event: string;
  data: T;
}

/**
 * SSE Streaming options
 */
export interface SSEStreamingOptions<T = unknown> {
  onEvent: (event: SSEEvent<T>) => void;
  onError: (error: Error) => void;
  signal?: AbortSignal;
}

/**
 * Parse SSE buffer into complete events and remaining buffer
 *
 * SSE format:
 * event: event_name
 * data: {"json": "data"}
 *
 * (blank line separates events)
 */
function parseSSEBuffer(buffer: string): {
  complete: SSEEvent[];
  remaining: string;
} {
  const complete: SSEEvent[] = [];
  const lines = buffer.split('\n');

  let currentEvent: { event?: string; data?: string } = {};
  let i = 0;
  let lastCompleteLineIndex = -1;

  while (i < lines.length) {
    const line = lines[i];

    // Empty line marks end of event
    if (line.trim() === '') {
      if (currentEvent.event && currentEvent.data) {
        try {
          complete.push({
            event: currentEvent.event,
            data: JSON.parse(currentEvent.data),
          });
          lastCompleteLineIndex = i;
        } catch (e) {
          console.error('Failed to parse SSE data:', currentEvent.data, e);
        }
        currentEvent = {};
      }
      i++;
      continue;
    }

    // Parse event field
    if (line.startsWith('event: ')) {
      currentEvent.event = line.substring(7).trim();
      i++;
      continue;
    }

    // Parse data field
    if (line.startsWith('data: ')) {
      currentEvent.data = line.substring(6).trim();
      i++;
      continue;
    }

    // Skip comments (lines starting with ':')
    if (line.startsWith(':')) {
      i++;
      continue;
    }

    // Skip unrecognized lines
    i++;
  }

  // Calculate remaining buffer (incomplete event)
  // Keep everything after the last complete event
  const remaining = lastCompleteLineIndex >= 0
    ? lines.slice(lastCompleteLineIndex + 1).join('\n')
    : buffer;

  return { complete, remaining };
}

/**
 * Make a streaming SSE request using native fetch
 * Parses Server-Sent Events format and emits structured events
 *
 * @param url - API endpoint path (relative to base URL)
 * @param body - Request body to send
 * @param options - SSE streaming callbacks and options
 */
export async function streamSSERequest<T = unknown>(
  url: string,
  body: Record<string, unknown>,
  options: SSEStreamingOptions<T>
): Promise<void> {
  const { onEvent, onError, signal } = options;

  try {
    const { token, sessionExpired } = await ensureFreshToken();
    if (sessionExpired) {
      onError(new Error(SESSION_EXPIRED_MESSAGE));
      return;
    }

    const requestInit: RequestInit = {
      method: 'POST',
      headers: {
        'x-request-id': generateRequestId(),
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        'client-name': 'pipeshub-ai',
        ...(token && { Authorization: `Bearer ${token}` }),
      },
      body: JSON.stringify(body),
      signal,
    };

    const response = isElectron()
      ? await streamingFetch(`${getApiBaseUrl()}${url}`, requestInit)
      : await fetch(`${API_BASE_URL}${url}`, requestInit);

    if (!response.ok) {
      throw new Error(`SSE request failed: ${response.status} ${response.statusText}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('No response body available for streaming');
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Parse SSE events from buffer
      const { complete, remaining } = parseSSEBuffer(buffer);

      // Process complete events
      for (const event of complete) {
        onEvent(event as SSEEvent<T>);
      }

      // Keep incomplete data in buffer
      buffer = remaining;
    }

    // Flush TextDecoder internal state; parse one more time so a trailing
    // complete event is not stranded across the final chunk boundary.
    buffer += decoder.decode(undefined, { stream: false });
    const { complete: tailEvents } = parseSSEBuffer(buffer);
    for (const event of tailEvents) {
      onEvent(event as SSEEvent<T>);
    }
  } catch (error) {
    // Don't report abort errors as actual errors
    if (error instanceof Error && error.name === 'AbortError') {
      return;
    }
    onError(error instanceof Error ? error : new Error('SSE request failed'));
  }
}

export interface SSEGetOptions<T = unknown> {
  onEvent: (event: SSEEvent<T>) => void;
  onError: (error: Error) => void;
  /** Extra request headers, e.g. `Last-Event-ID` for resume on reconnect. */
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /**
   * Abort the stream if no bytes arrive for this long ONCE STREAMING HAS BEGUN
   * (i.e. between SSE events, after the response headers arrive). The server
   * heartbeats every ~15s during long work, so silence beyond this means the
   * connection died mid-stream. This deliberately does NOT bound the upload /
   * time-to-first-byte phase — see the note in `streamSSEUpload`. Surfaced via
   * `onError` so the caller can fail/retry instead of hanging forever.
   */
  idleTimeoutMs?: number;
}

const DEFAULT_UPLOAD_IDLE_TIMEOUT_MS = 60_000;

/**
 * POST a multipart upload and consume its Server-Sent Events response. The KB
 * upload endpoints stream a per-file outcome (`file:succeeded` / `file:failed`)
 * and a final `done` on the SAME response, so the upload and its progress are a
 * single request. `FormData` is sent as the body; do NOT set `Content-Type` (the
 * browser adds the multipart boundary).
 */
export async function streamSSEUpload<T = unknown>(
  url: string,
  formData: FormData,
  options: SSEGetOptions<T>,
): Promise<void> {
  const {
    onEvent,
    onError,
    headers: extraHeaders,
    signal,
    idleTimeoutMs = DEFAULT_UPLOAD_IDLE_TIMEOUT_MS,
  } = options;

  // Own controller so we can abort on idle timeout; chained to any caller signal.
  const controller = new AbortController();
  let idleTimedOut = false;
  const onExternalAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', onExternalAbort, { once: true });
  }

  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  const resetIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      idleTimedOut = true;
      controller.abort();
    }, idleTimeoutMs);
  };
  const clearIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = undefined;
  };

  try {
    const { token, sessionExpired } = await ensureFreshToken();
    if (sessionExpired) {
      onError(new Error(SESSION_EXPIRED_MESSAGE));
      return;
    }

    const requestInit: RequestInit = {
      method: 'POST',
      headers: {
        'x-request-id': generateRequestId(),
        Accept: 'text/event-stream',
        ...(extraHeaders || {}),
        ...(token && { Authorization: `Bearer ${token}` }),
      },
      body: formData,
      signal: controller.signal,
    };

    // Do NOT arm the idle watchdog here. Until the server emits its first SSE
    // byte NO response bytes flow, and it cannot do so until multer has
    // buffered the ENTIRE multipart body. A large upload over real bandwidth
    // (or through Cloudflare) easily exceeds `idleTimeoutMs` during this
    // window, so arming the timer around `fetch` would abort a perfectly
    // healthy upload — the "times out after 60s on the cloud" regression. The
    // upload / time-to-first-byte phase is intentionally left unbounded here,
    // matching the previous axios `timeout: 0` behaviour that worked.
    const response = isElectron()
      ? await streamingFetch(`${getApiBaseUrl()}${url}`, requestInit)
      : await fetch(`${API_BASE_URL}${url}`, requestInit);

    if (!response.ok) {
      // Rate limit / payload-too-large / 5xx arrive as a normal JSON response
      // (the rate limiter short-circuits before the SSE handler). Surface the
      // server's exact error so the user knows the real cause.
      throw await readUploadHttpError(response);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('No response body available for streaming');
    }

    // Streaming has begun (200 + readable body). NOW arm the idle watchdog:
    // from here the server heartbeats every ~15s, so silence beyond
    // `idleTimeoutMs` means the stream died. This bounds the headers->first-byte
    // gap and every inter-event gap, but never the upload itself.
    resetIdle();

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      resetIdle();
      buffer += decoder.decode(value, { stream: true });
      const { complete, remaining } = parseSSEBuffer(buffer);
      for (const event of complete) {
        onEvent(event as SSEEvent<T>);
      }
      buffer = remaining;
    }

    buffer += decoder.decode(undefined, { stream: false });
    const { complete: tailEvents } = parseSSEBuffer(buffer);
    for (const event of tailEvents) {
      onEvent(event as SSEEvent<T>);
    }
  } catch (error) {
    // A real HTTP error (rate limit, payload too large, 5xx) always takes
    // precedence over the idle watchdog — never report it as a timeout.
    if (error instanceof UploadHttpError) {
      onError(error);
      return;
    }
    if (idleTimedOut) {
      onError(new Error('Upload timed out (no response from server)'));
      return;
    }
    // External (caller) abort — silent, like the other streamers.
    if (error instanceof Error && error.name === 'AbortError') {
      return;
    }
    onError(error instanceof Error ? error : new Error('Upload failed'));
  } finally {
    clearIdle();
    if (signal) signal.removeEventListener('abort', onExternalAbort);
  }
}

/**
 * GET variant of {@link streamSSERequest} for long-lived, reconnectable streams.
 * EventSource cannot send an Authorization header, so we use fetch +
 * ReadableStream like the chat stream, and let callers supply `Last-Event-ID` to
 * resume after a drop. Returns when the stream ends; the caller decides
 * whether/when to reconnect. (Upload progress is served by the POST-response
 * stream in `streamSSEUpload`, not by this helper.)
 */
export async function streamSSEGet<T = unknown>(
  url: string,
  options: SSEGetOptions<T>,
): Promise<void> {
  const { onEvent, onError, headers: extraHeaders, signal } = options;

  try {
    const { token, sessionExpired } = await ensureFreshToken();
    if (sessionExpired) {
      onError(new Error(SESSION_EXPIRED_MESSAGE));
      return;
    }

    const requestInit: RequestInit = {
      method: 'GET',
      headers: {
        'x-request-id': generateRequestId(),
        Accept: 'text/event-stream',
        ...(extraHeaders || {}),
        ...(token && { Authorization: `Bearer ${token}` }),
      },
      signal,
    };

    const response = isElectron()
      ? await streamingFetch(`${getApiBaseUrl()}${url}`, requestInit)
      : await fetch(`${API_BASE_URL}${url}`, requestInit);

    if (!response.ok) {
      throw new Error(`SSE request failed: ${response.status} ${response.statusText}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('No response body available for streaming');
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { complete, remaining } = parseSSEBuffer(buffer);
      for (const event of complete) {
        onEvent(event as SSEEvent<T>);
      }
      buffer = remaining;
    }

    buffer += decoder.decode(undefined, { stream: false });
    const { complete: tailEvents } = parseSSEBuffer(buffer);
    for (const event of tailEvents) {
      onEvent(event as SSEEvent<T>);
    }
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return;
    }
    onError(error instanceof Error ? error : new Error('SSE request failed'));
  }
}
