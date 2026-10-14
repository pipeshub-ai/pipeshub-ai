/** Test helpers that fake the network side of a streamed response. */

export interface StreamPlan {
  /** Pieces of the body, delivered one `read()` at a time. */
  chunks?: string[];
  /** Fail the body after the chunks, as a dropped connection does. */
  failWith?: Error;
  /** Never close: the body hangs after the chunks until aborted. */
  hang?: boolean;
  /** Error the body when this aborts, as a browser's fetch does. */
  signal?: AbortSignal | null;
}

export function streamBody({ chunks = [], failWith, hang, signal }: StreamPlan): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    start(controller) {
      signal?.addEventListener('abort', () => controller.error(abortError()), { once: true });
    },
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i++]));
        return;
      }
      if (failWith) {
        controller.error(failWith);
        return;
      }
      if (hang) return new Promise<void>(() => {});
      controller.close();
    },
  });
}

export function sseResponse(plan: StreamPlan | string[], init: ResponseInit = {}): Response {
  const p = Array.isArray(plan) ? { chunks: plan } : plan;
  return new Response(streamBody(p), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
    ...init,
  });
}

export function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

/** One SSE frame in the wire format the backend writes. */
export function sseFrame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** An unsigned JWT whose only claim is `exp`, `secondsFromNow` ahead. */
export function jwtExpiringIn(secondsFromNow: number): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + secondsFromNow }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `header.${payload}.signature`;
}

/** A working in-memory Storage, for environments whose global one throws. */
export function installMemoryStorage(target: 'localStorage' | 'sessionStorage' = 'localStorage'): Map<string, string> {
  const backing = new Map<string, string>();
  Object.defineProperty(window, target, {
    configurable: true,
    value: {
      getItem: (key: string) => (backing.has(key) ? (backing.get(key) as string) : null),
      setItem: (key: string, value: string) => backing.set(key, String(value)),
      removeItem: (key: string) => backing.delete(key),
      clear: () => backing.clear(),
      key: (index: number) => Array.from(backing.keys())[index] ?? null,
      get length() {
        return backing.size;
      },
    },
  });
  return backing;
}

/**
 * What a browser's fetch throws on abort. jsdom's own `DOMException` is not an
 * `Error` subclass, unlike every browser's, so build the browser shape.
 */
export function abortError(): Error {
  return Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' });
}
