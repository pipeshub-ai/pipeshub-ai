/**
 * In Electron, the renderer runs under the app:// origin and Chromium's
 * CORS + streaming behavior is unreliable for long-lived SSE responses to
 * a cross-origin backend. The preload exposes a callback-based
 * `streamFetch` that proxies the request through the main process (no
 * CORS). We reconstruct a Response here on the renderer side so callers
 * can use `response.body.getReader()` as with native fetch.
 *
 * (Returning a Response/ReadableStream directly from the preload doesn't
 * work — contextBridge strips instance methods from cloned objects.)
 */

type ElectronStreamCallbacks = {
  onHeaders: (h: { ok: boolean; status: number; statusText: string; headers: Record<string, string> }) => void;
  onChunk: (chunk: Uint8Array) => void;
  onEnd: () => void;
  onError: (err: { name: string; message: string }) => void;
};

type ElectronStreamFetch = (
  url: string,
  init: { method?: string; headers?: Record<string, string>; body?: string },
  callbacks: ElectronStreamCallbacks
) => () => void;

/**
 * Returns the Electron preload's `streamFetch` if available, otherwise null.
 * Web builds always get null.
 */
function getElectronStreamFetch(): ElectronStreamFetch | null {
  return (
    (globalThis as unknown as { electronAPI?: { streamFetch?: ElectronStreamFetch } })
      .electronAPI?.streamFetch ?? null
  );
}

/**
 * Drop-in replacement for `fetch` that routes streaming requests through
 * Electron's main-process proxy when running under Electron, and falls back
 * to native `fetch` everywhere else.
 */
export function streamingFetch(url: string, init: RequestInit): Promise<Response> {
  const electronStreamFetch = getElectronStreamFetch();

  if (!electronStreamFetch) {
    return fetch(url, init);
  }

  return new Promise<Response>((resolve, reject) => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    let headersReceived = false;

    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
      cancel() {
        abort();
      },
    });

    const abort = electronStreamFetch(
      url,
      {
        method: init.method,
        headers: init.headers as Record<string, string> | undefined,
        body: init.body as string | undefined,
      },
      {
        onHeaders: (h) => {
          if (headersReceived) return;
          headersReceived = true;
          resolve(
            new Response(body, {
              status: h.status,
              statusText: h.statusText,
              headers: h.headers,
            })
          );
        },
        onChunk: (chunk) => {
          streamController?.enqueue(chunk);
        },
        onEnd: () => {
          streamController?.close();
        },
        onError: (err) => {
          const e = new Error(err.message);
          e.name = err.name;
          if (!headersReceived) {
            reject(e);
          } else {
            try { streamController?.error(e); } catch { /* already closed */ }
          }
        },
      }
    );

    if (init.signal) {
      if (init.signal.aborted) {
        abort();
      } else {
        init.signal.addEventListener('abort', abort, { once: true });
      }
    }
  });
}
