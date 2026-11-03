import type { Page } from '@playwright/test';

type OpenStreamEntry = { source: string; flags: string; body: string };
type OpenStreamWindow = Window & {
  __e2eOpenSseStreams?: OpenStreamEntry[];
  __e2eOpenSseInstalled?: boolean;
};

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Answers the next POST whose URL path matches `path` with an SSE response
 * that sends `body` and then stays open, like a run the backend is still
 * working on. `route.fulfill` can't do that: it always ends the response, and
 * the chat reports a stream that ends without `RUN_FINISHED` or `RUN_ERROR`
 * as interrupted. This response ends only when the page aborts the request
 * (Stop's grace timeout does), with the `AbortError` a real fetch gives.
 *
 * It wraps `window.fetch` in the current page, so call it after navigating.
 */
export async function serveOpenSseStream(page: Page, path: string | RegExp, body: string): Promise<void> {
  const pattern =
    typeof path === 'string'
      ? { source: `^${escapeRegExp(path)}$`, flags: '' }
      : { source: path.source, flags: path.flags };

  await page.evaluate(({ source, flags, body: sseBody }) => {
    const w = window as OpenStreamWindow;
    w.__e2eOpenSseStreams ??= [];
    w.__e2eOpenSseStreams.push({ source, flags, body: sseBody });
    if (w.__e2eOpenSseInstalled) return;
    w.__e2eOpenSseInstalled = true;

    const realFetch = window.fetch.bind(window);
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : null;
      const url = new URL(request ? request.url : String(input), window.location.href);
      const method = (init?.method ?? request?.method ?? 'GET').toUpperCase();
      const streams = w.__e2eOpenSseStreams ?? [];
      const index =
        method === 'POST' ? streams.findIndex((s) => new RegExp(s.source, s.flags).test(url.pathname)) : -1;
      if (index < 0) return realFetch(input, init);

      const [entry] = streams.splice(index, 1);
      const signal = init?.signal ?? request?.signal;
      const aborted = () => new DOMException('The user aborted a request.', 'AbortError');
      if (signal?.aborted) return Promise.reject(aborted());

      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(entry.body));
          signal?.addEventListener('abort', () => controller.error(aborted()), { once: true });
        },
      });
      return Promise.resolve(
        new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
      );
    };
  }, { ...pattern, body });
}
