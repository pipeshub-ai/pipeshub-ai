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

type ElectronStreamBody = string | Uint8Array;

type ElectronStreamFetch = (
  url: string,
  init: { method?: string; headers?: Record<string, string>; body?: ElectronStreamBody },
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

function plainHeaders(headers: HeadersInit | undefined): Record<string, string> {
  if (!headers) return {};
  return Object.fromEntries(new Headers(headers).entries());
}

/**
 * IPC structured-clone cannot carry FormData, Blob or stream bodies (a FormData
 * arrives in the main process as `{}`, so multipart uploads lose every file).
 * Encode such bodies to bytes here, keeping the Content-Type that carries the
 * multipart boundary.
 */
async function serializeBody(
  body: BodyInit | null | undefined,
  headers: Record<string, string>
): Promise<ElectronStreamBody | undefined> {
  if (body == null) return undefined;
  if (typeof body === 'string') return body;

  const encoded = new Response(body);
  const contentType = encoded.headers.get('content-type');
  // The bytes use the encoder's multipart boundary, so its header wins over a
  // caller's (e.g. a bare `multipart/form-data` with no boundary).
  const isMultipart = contentType?.startsWith('multipart/form-data') ?? false;
  if (contentType && (isMultipart || !('content-type' in headers))) {
    headers['content-type'] = contentType;
  }
  return new Uint8Array(await encoded.arrayBuffer());
}

/**
 * Drop-in replacement for `fetch` that routes streaming requests through
 * Electron's main-process proxy when running under Electron, and falls back
 * to native `fetch` everywhere else.
 */
export async function streamingFetch(url: string, init: RequestInit): Promise<Response> {
  const electronStreamFetch = getElectronStreamFetch();

  if (!electronStreamFetch) {
    return fetch(url, init);
  }

  const headers = plainHeaders(init.headers);
  const requestBody = await serializeBody(init.body, headers);

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
        headers,
        body: requestBody,
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
