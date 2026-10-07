/**
 * The SSE transport under chat answers, KB uploads and model downloads:
 * frame parsing across arbitrary chunk boundaries, the token handshake, and
 * the message a person reads when a stream fails. Only `fetch` is faked.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installMemoryStorage,
  abortError,
  jsonResponse,
  jwtExpiringIn,
  sseFrame,
  sseResponse,
} from './sse-response';

installMemoryStorage();

const logoutAndRedirect = vi.fn();
vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: () => logoutAndRedirect() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const {
  streamSSERequest,
  streamSSEUpload,
  streamSSEGet,
  streamRequest,
  createStreamController,
  UploadHttpError,
  UPLOAD_STALLED_MESSAGE,
} = await import('../streaming');
const { CHAT_STREAM_ERROR_MESSAGES, STREAM_ERROR_MESSAGES, busyStreamMessage } = await import('../stream-errors');
const { REFRESH_TOKEN_ENDPOINT } = await import('../token-refresh');

const fetchMock = vi.fn<typeof fetch>();

function collect() {
  const events: Array<{ event: string; data: unknown }> = [];
  const errors: Error[] = [];
  return {
    events,
    errors,
    onEvent: (e: { event: string; data: unknown }) => events.push(e),
    onError: (e: Error) => errors.push(e),
  };
}

function requestHeaders(callIndex = 0): Record<string, string> {
  return fetchMock.mock.calls[callIndex][1]?.headers as Record<string, string>;
}

beforeEach(() => {
  fetchMock.mockReset();
  logoutAndRedirect.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'refresh-1' });
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('streamSSERequest frame parsing', () => {
  it('delivers each event once, in order, however the bytes are split', async () => {
    const wire =
      sseFrame('RUN_STARTED', { type: 'RUN_STARTED' }) +
      sseFrame('TEXT_MESSAGE_CONTENT', { type: 'TEXT_MESSAGE_CONTENT', delta: 'Hello, wörld' }) +
      sseFrame('RUN_FINISHED', { type: 'RUN_FINISHED', result: { ok: true } });
    // Split mid-keyword, mid-JSON, between the two newlines, and one byte at a time at the end.
    const cuts = [3, 20, 47, 48, 70, wire.length - 5];
    const chunks: string[] = [];
    let prev = 0;
    for (const cut of cuts) {
      chunks.push(wire.slice(prev, cut));
      prev = cut;
    }
    for (const ch of wire.slice(prev)) chunks.push(ch);

    fetchMock.mockResolvedValueOnce(sseResponse(chunks));
    const sink = collect();
    await streamSSERequest('/api/v1/conversations/stream', { query: 'hi' }, sink);

    expect(sink.errors).toEqual([]);
    expect(sink.events.map((e) => e.event)).toEqual(['RUN_STARTED', 'TEXT_MESSAGE_CONTENT', 'RUN_FINISHED']);
    expect(sink.events[1].data).toEqual({ type: 'TEXT_MESSAGE_CONTENT', delta: 'Hello, wörld' });
  });

  it('keeps a multi-byte character intact when it is split across chunks', async () => {
    const bytes = new TextEncoder().encode(sseFrame('x', { text: '日本' }));
    const splitAt = bytes.indexOf(0xe6) + 1; // inside the first 3-byte character
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, splitAt));
        controller.enqueue(bytes.slice(splitAt));
        controller.close();
      },
    });
    fetchMock.mockResolvedValueOnce(new Response(body, { status: 200 }));
    const sink = collect();
    await streamSSERequest('/s', {}, sink);
    expect(sink.events).toEqual([{ event: 'x', data: { text: '日本' } }]);
  });

  it('accepts CRLF line endings and skips keep-alive comments', async () => {
    fetchMock.mockResolvedValueOnce(
      sseResponse([': connected\r\n\r\n', 'event: status\r\ndata: {"step":1}\r\n\r\n', ': keepalive\n\n', 'event: status\ndata: {"step":2}\n\n']),
    );
    const sink = collect();
    await streamSSERequest('/s', {}, sink);
    expect(sink.events.map((e) => e.data)).toEqual([{ step: 1 }, { step: 2 }]);
  });

  it('drops a frame whose data is not JSON and still delivers the ones after it', async () => {
    fetchMock.mockResolvedValueOnce(
      sseResponse(['event: a\ndata: {"n":1}\n\nevent: bad\ndata: {not json\n\nevent: c\ndata: {"n":3}\n\n']),
    );
    const sink = collect();
    await streamSSERequest('/s', {}, sink);
    expect(sink.events.map((e) => e.event)).toEqual(['a', 'c']);
    expect(sink.errors).toEqual([]);
  });

  it('delivers a final frame that arrives in the very last chunk', async () => {
    fetchMock.mockResolvedValueOnce(sseResponse(['event: a\ndata: {}\n\nevent: last\ndata: {"end":true}', '\n\n']));
    const sink = collect();
    await streamSSERequest('/s', {}, sink);
    expect(sink.events.map((e) => e.event)).toEqual(['a', 'last']);
  });

  it('sends the chat request as JSON with the bearer token and SSE accept header', async () => {
    fetchMock.mockResolvedValueOnce(sseResponse([]));
    const token = useAuthStore.getState().accessToken;
    await streamSSERequest('/api/v1/conversations/stream', { query: 'hi' }, collect());

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/conversations/stream');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({ query: 'hi' });
    expect(requestHeaders()).toMatchObject({
      Authorization: `Bearer ${token}`,
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    });
    expect(requestHeaders()['x-request-id']).toBeTruthy();
  });

  it('sends no Authorization header when nobody is signed in', async () => {
    useAuthStore.setState({ accessToken: null });
    fetchMock.mockResolvedValueOnce(sseResponse([]));
    await streamSSERequest('/s', {}, collect());
    expect(requestHeaders()).not.toHaveProperty('Authorization');
  });
});

describe('token handshake before a stream', () => {
  it('refreshes a token about to expire, then streams with the new one', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(30), refreshToken: 'refresh-1' });
    const fresh = jwtExpiringIn(3600);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }))
      .mockResolvedValueOnce(sseResponse([sseFrame('ok', {})]));

    const sink = collect();
    await streamSSERequest('/s', {}, sink);

    expect(fetchMock.mock.calls[0][0]).toBe(REFRESH_TOKEN_ENDPOINT);
    expect((fetchMock.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBe('Bearer refresh-1');
    expect(requestHeaders(1).Authorization).toBe(`Bearer ${fresh}`);
    expect(sink.events).toHaveLength(1);
  });

  it('signs the person out and says so when the refresh is refused', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(30), refreshToken: 'refresh-1' });
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));

    const sink = collect();
    await streamSSERequest('/s', {}, sink);

    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1); // the stream itself was never requested
    expect(sink.errors.map((e) => e.message)).toEqual([STREAM_ERROR_MESSAGES.sessionExpired]);
  });
});

describe('what a person reads when the chat stream fails', () => {
  it.each([
    ['401', jsonResponse(401, { message: 'jwt expired' }), CHAT_STREAM_ERROR_MESSAGES.sessionExpired],
    ['429 with Retry-After', jsonResponse(429, {}, { 'retry-after': '7' }), busyStreamMessage(7)],
    ['503 without a hint', jsonResponse(503, {}), busyStreamMessage()],
    ['403 with a readable reason', jsonResponse(403, { message: 'This agent was shared with view-only access.' }), 'This agent was shared with view-only access.'],
    ['403 with no reason', jsonResponse(403, {}), CHAT_STREAM_ERROR_MESSAGES.forbidden],
    ['400 with a traceback', jsonResponse(400, { message: "KeyError: 'model'" }), CHAT_STREAM_ERROR_MESSAGES.unavailable],
    ['500 HTML page from a proxy', new Response('<html>Bad gateway</html>', { status: 500 }), CHAT_STREAM_ERROR_MESSAGES.unavailable],
  ])('%s', async (_label, response, expected) => {
    fetchMock.mockResolvedValueOnce(response);
    const sink = collect();
    await streamSSERequest('/s', {}, sink);
    expect(sink.errors.map((e) => e.message)).toEqual([expected]);
    expect(sink.events).toEqual([]);
  });

  it('says PipesHub could not be reached when the request never leaves the browser', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const sink = collect();
    await streamSSERequest('/s', {}, sink);
    expect(sink.errors.map((e) => e.message)).toEqual([CHAT_STREAM_ERROR_MESSAGES.offline]);
  });

  it('keeps the frames that arrived and says the answer was interrupted when the connection drops', async () => {
    fetchMock.mockResolvedValueOnce(
      sseResponse({ chunks: [sseFrame('TEXT_MESSAGE_CONTENT', { delta: 'Part' })], failWith: new TypeError('network error') }),
    );
    const sink = collect();
    await streamSSERequest('/s', {}, sink);
    expect(sink.events).toHaveLength(1);
    expect(sink.errors.map((e) => e.message)).toEqual([CHAT_STREAM_ERROR_MESSAGES.interrupted]);
  });

  it('stays silent when the caller aborts', async () => {
    const { signal, abort } = createStreamController();
    fetchMock.mockImplementationOnce(async (_url, init) => {
      const s = init?.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        if (s.aborted) reject(abortError());
        s.addEventListener('abort', () => reject(abortError()));
      });
    });
    const sink = collect();
    const done = streamSSERequest('/s', {}, { ...sink, signal });
    abort();
    await done;
    expect(sink.errors).toEqual([]);
  });
});

describe('streamSSEUpload', () => {
  const form = () => {
    const fd = new FormData();
    fd.append('files', new Blob(['hello']), 'a.txt');
    return fd;
  };

  it('posts the form as-is (no JSON content type) and streams per-file events', async () => {
    fetchMock.mockResolvedValueOnce(
      sseResponse([sseFrame('file:succeeded', { filePath: 'a.txt' }), ': keepalive\n\n', sseFrame('done', {})]),
    );
    const sink = collect();
    const fd = form();
    await streamSSEUpload('/api/v1/knowledgebase/kb1/upload', fd, { ...sink, headers: { 'Last-Event-ID': '4' } });

    const init = fetchMock.mock.calls[0][1];
    expect(init?.body).toBe(fd);
    expect(requestHeaders()).not.toHaveProperty('Content-Type');
    expect(requestHeaders()['Last-Event-ID']).toBe('4');
    expect(sink.events.map((e) => e.event)).toEqual(['file:succeeded', 'done']);
    expect(sink.errors).toEqual([]);
  });

  it("reports the server's exact rate-limit reason and wait", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(429, { error: { code: 'RATE_LIMITED', message: 'Too many uploads. Wait a minute, then try again.', retryAfter: 60 } }),
    );
    const sink = collect();
    await streamSSEUpload('/u', form(), sink);

    const [err] = sink.errors;
    expect(err).toBeInstanceOf(UploadHttpError);
    expect(err).toMatchObject({ status: 429, code: 'RATE_LIMITED', retryAfter: 60, message: 'Too many uploads. Wait a minute, then try again.' });
  });

  it('falls back to the Retry-After header and a busy message when the body is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response('busy', { status: 503, headers: { 'retry-after': '9' } }));
    const sink = collect();
    await streamSSEUpload('/u', form(), sink);
    expect(sink.errors[0]).toMatchObject({ status: 503, retryAfter: 9, message: 'PipesHub is busy right now. Please try again in 9 seconds.' });
  });

  it('keeps the full server wait even when it is too long to quote in the message', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 429, headers: { 'retry-after': '600' } }));
    const sink = collect();
    await streamSSEUpload('/u', form(), sink);
    expect(sink.errors[0]).toMatchObject({ retryAfter: 600, message: 'PipesHub is busy right now. Please try again in a few seconds.' });
  });

  it('gives a plain next step for other refusals', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 413 }));
    const sink = collect();
    await streamSSEUpload('/u', form(), sink);
    expect(sink.errors[0].message).toMatch(/didn't go through.*try again/);
  });

  it('does not time out while the upload itself is still being sent', async () => {
    fetchMock.mockImplementationOnce(
      () => new Promise((resolve) => setTimeout(() => resolve(sseResponse([sseFrame('done', {})])), 80)),
    );
    const sink = collect();
    await streamSSEUpload('/u', form(), { ...sink, idleTimeoutMs: 20 });
    expect(sink.errors).toEqual([]);
    expect(sink.events.map((e) => e.event)).toEqual(['done']);
  });

  it('fails instead of hanging when the server goes quiet mid-stream', async () => {
    fetchMock.mockImplementationOnce(async (_url, init) => {
      const signal = init?.signal as AbortSignal;
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(sseFrame('file:succeeded', { filePath: 'a.txt' })));
          signal.addEventListener('abort', () => controller.error(abortError()));
        },
      });
      return new Response(body, { status: 200 });
    });
    const sink = collect();
    await streamSSEUpload('/u', form(), { ...sink, idleTimeoutMs: 30 });
    expect(sink.events).toHaveLength(1);
    expect(sink.errors.map((e) => e.message)).toEqual([UPLOAD_STALLED_MESSAGE]);
  });

  // Each message lands on every affected file's row in the upload tracker.
  it("says PipesHub couldn't be reached, not the browser's own words, when the upload never leaves", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const sink = collect();
    await streamSSEUpload('/u', form(), sink);
    expect(sink.errors.map((e) => e.message)).toEqual([STREAM_ERROR_MESSAGES.offline]);
  });

  it('says the connection dropped when it breaks after the server started answering', async () => {
    fetchMock.mockResolvedValueOnce(
      sseResponse({ chunks: [sseFrame('file:succeeded', { filePath: 'a.txt' })], failWith: new TypeError('network error') }),
    );
    const sink = collect();
    await streamSSEUpload('/u', form(), sink);
    expect(sink.events).toHaveLength(1);
    expect(sink.errors.map((e) => e.message)).toEqual([STREAM_ERROR_MESSAGES.interrupted]);
  });

  it("passes on an error thrown by the caller's own event handler, not a connection message", async () => {
    fetchMock.mockResolvedValueOnce(sseResponse([sseFrame('file:succeeded', { filePath: 'a.txt' }), sseFrame('done', {})]));
    const bug = new TypeError("Cannot read properties of undefined (reading 'id')");
    const errors: Error[] = [];
    await streamSSEUpload('/u', form(), {
      onEvent: () => {
        throw bug;
      },
      onError: (e) => errors.push(e),
    });
    expect(errors).toHaveLength(1);
    expect(errors[0]).toBe(bug);
  });

  it('stays silent when the caller cancels, including before it starts', async () => {
    const controller = new AbortController();
    controller.abort();
    fetchMock.mockImplementationOnce(async (_url, init) => {
      if ((init?.signal as AbortSignal).aborted) throw abortError();
      return sseResponse([]);
    });
    const sink = collect();
    await streamSSEUpload('/u', form(), { ...sink, signal: controller.signal });
    expect(sink.errors).toEqual([]);
  });
});

describe('streamSSEGet', () => {
  it('opens a GET stream with the extra headers', async () => {
    fetchMock.mockResolvedValueOnce(sseResponse([sseFrame('progress', { pct: 50 }), sseFrame('progress', { pct: 100 })]));
    const sink = collect();
    await streamSSEGet('/api/v1/models/download/progress', { ...sink, headers: { 'Last-Event-ID': '12' } });
    expect(fetchMock.mock.calls[0][1]?.method).toBe('GET');
    expect(requestHeaders()['Last-Event-ID']).toBe('12');
    expect(sink.events.map((e) => e.data)).toEqual([{ pct: 50 }, { pct: 100 }]);
  });

  it('uses the general (not chat) wording when it fails', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));
    const sink = collect();
    await streamSSEGet('/p', sink);
    expect(sink.errors.map((e) => e.message)).toEqual([STREAM_ERROR_MESSAGES.unavailable]);
  });
});

describe('streamRequest (raw text stream)', () => {
  it('hands over raw text chunks and then completes', async () => {
    fetchMock.mockResolvedValueOnce(sseResponse(['Hel', 'lo']));
    const chunks: string[] = [];
    const onComplete = vi.fn();
    const onError = vi.fn();
    await streamRequest('/raw', { a: 1 }, { onChunk: (c) => chunks.push(c), onComplete, onError });
    expect(chunks.join('')).toBe('Hello');
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it('reports a refused request without completing', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    const onComplete = vi.fn();
    const onError = vi.fn();
    await streamRequest('/raw', {}, { onChunk: vi.fn(), onComplete, onError });
    expect(onComplete).not.toHaveBeenCalled();
    expect(onError.mock.calls[0][0].message).toBe(STREAM_ERROR_MESSAGES.sessionExpired);
  });
});
