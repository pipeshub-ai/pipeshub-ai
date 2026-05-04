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

import { useAuthStore } from '@/lib/store/auth-store';

// Default to '' (same origin) rather than `undefined`, because template-string
// concatenation like `${API_BASE_URL}${url}` would otherwise stringify
// `undefined` and produce URLs like `/chat/undefined/api/v1/...`. In our
// standard deployment the Next.js static export is served by the Node.js
// backend, so the API is always same-origin and an empty prefix is correct.
// Override with `NEXT_PUBLIC_API_BASE_URL` at build time for split deployments.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

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
    const token = useAuthStore.getState().accessToken;

    const response = await fetch(`${API_BASE_URL}${url}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token && { Authorization: `Bearer ${token}` }),
      },
      body: JSON.stringify(body),
      signal,
    });

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
    const token = useAuthStore.getState().accessToken;

    const response = await fetch(`${API_BASE_URL}${url}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        ...(token && { Authorization: `Bearer ${token}` }),
      },
      body: JSON.stringify(body),
      signal,
    });

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
