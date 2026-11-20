import { describe, expect, it } from 'vitest';
import {
  CHAT_STREAM_ERROR_MESSAGES,
  STREAM_ERROR_MESSAGES,
  StreamError,
  streamFailure,
  streamHttpError,
} from '../stream-errors';

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: 'Status Text',
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

describe('streamHttpError', () => {
  it('asks the user to wait on a 429, using Retry-After when present', async () => {
    const err = await streamHttpError(
      jsonResponse(429, { error: { message: 'Too many requests' } }, { 'Retry-After': '12' }),
      CHAT_STREAM_ERROR_MESSAGES,
    );
    expect(err).toBeInstanceOf(StreamError);
    expect(err.message).toBe('PipesHub is busy right now. Please try again in 12 seconds.');
    expect(err.message).not.toContain('429');
  });

  it('says the session expired on a 401', async () => {
    const err = await streamHttpError(jsonResponse(401, {}), CHAT_STREAM_ERROR_MESSAGES);
    expect(err.message).toBe(CHAT_STREAM_ERROR_MESSAGES.sessionExpired);
  });

  it("keeps a clear 4xx message from the server", async () => {
    const err = await streamHttpError(
      jsonResponse(400, { error: { message: 'No AI model is set up for this workspace yet.' } }),
      CHAT_STREAM_ERROR_MESSAGES,
    );
    expect(err.message).toBe('No AI model is set up for this workspace yet.');
  });

  it('replaces technical server text with a plain message', async () => {
    const forbidden = await streamHttpError(
      jsonResponse(403, { detail: "KeyError: 'orgId'" }),
      CHAT_STREAM_ERROR_MESSAGES,
    );
    expect(forbidden.message).toBe(CHAT_STREAM_ERROR_MESSAGES.forbidden);

    const server = await streamHttpError(
      jsonResponse(500, { error: { message: 'Internal server error' } }),
      CHAT_STREAM_ERROR_MESSAGES,
    );
    expect(server.message).toBe(CHAT_STREAM_ERROR_MESSAGES.unavailable);
    expect(server.message).not.toContain('500');
  });

  it('copes with a body that is not JSON', async () => {
    const html = new Response('<html>Bad gateway</html>', { status: 502, statusText: 'Bad Gateway' });
    const err = await streamHttpError(html);
    expect(err.message).toBe(STREAM_ERROR_MESSAGES.unavailable);
  });
});

describe('streamFailure', () => {
  it('says PipesHub could not be reached when the request never got an answer', () => {
    const err = streamFailure(new TypeError('Failed to fetch'), false, CHAT_STREAM_ERROR_MESSAGES);
    expect(err.message).toBe(CHAT_STREAM_ERROR_MESSAGES.offline);
  });

  it('says the answer was interrupted when the connection dropped mid-answer', () => {
    const err = streamFailure(new TypeError('network error'), true, CHAT_STREAM_ERROR_MESSAGES);
    expect(err.message).toBe(CHAT_STREAM_ERROR_MESSAGES.interrupted);
  });

  it('uses neutral wording outside chat', () => {
    expect(streamFailure(new Error('terminated'), true).message).toBe(STREAM_ERROR_MESSAGES.interrupted);
    expect(STREAM_ERROR_MESSAGES.interrupted).not.toContain('Regenerate');
  });

  it('passes through an error already written for the user', () => {
    const own = new StreamError('Your session has expired. Sign in again to continue.', 401);
    expect(streamFailure(own, false)).toBe(own);
  });
});

describe('streamHttpError status branches (characterization)', () => {
  const M = STREAM_ERROR_MESSAGES;
  const cases: Array<[string, number, unknown, string, Record<string, string>?]> = [
    ['401 session expired', 401, { error: { message: 'Token bad' } }, M.sessionExpired],
    ['429 busy no header', 429, {}, 'PipesHub is busy right now. Please try again in a few seconds.'],
    ['503 busy with Retry-After', 503, {}, 'PipesHub is busy right now. Please try again in 7 seconds.', { 'Retry-After': '7' }],
    ['504 busy', 504, { message: 'upstream' }, 'PipesHub is busy right now. Please try again in a few seconds.'],
    ['400 readable server message', 400, { message: 'Attachment is too large.' }, 'Attachment is too large.'],
    ['409 readable server message', 409, { error: { code: 'X', message: 'Busy, try later.' } }, 'Busy, try later.'],
    ['403 technical text -> forbidden', 403, { detail: "KeyError: 'orgId'" }, M.forbidden],
    ['403 no body -> forbidden', 403, {}, M.forbidden],
    ['404 no body -> unavailable', 404, {}, M.unavailable],
    ['500 readable text is not shown', 500, { message: 'Something readable' }, M.unavailable],
    ['502 -> unavailable', 502, {}, M.unavailable],
  ];

  it.each(cases)('%s', async (_name, status, body, message, headers) => {
    const err = await streamHttpError(jsonResponse(status, body, headers));
    expect(err).toBeInstanceOf(StreamError);
    expect(err.message).toBe(message);
    expect(err.status).toBe(status);
  });

  it('streamFailure builds an error without a status', () => {
    expect(streamFailure(new TypeError('x'), false).status).toBeUndefined();
  });
});

describe('streamHttpError code and details', () => {
  it('carries code and details from a structured body on any branch', async () => {
    const body = { error: { code: 'CONVERSATION_BUSY', message: 'Busy', details: { x: 1 } } };
    for (const status of [401, 403, 409, 429, 500]) {
      const err = await streamHttpError(jsonResponse(status, body));
      expect(err.code).toBe('CONVERSATION_BUSY');
      expect(err.details).toEqual({ x: 1 });
    }
  });

  it('prefers top-level details', async () => {
    const err = await streamHttpError(jsonResponse(409, { details: { t: 1 }, error: { code: 'C', details: { n: 1 } } }));
    expect(err.details).toEqual({ t: 1 });
  });

  it('leaves code undefined for an HTML or string-error body', async () => {
    const html = await streamHttpError(new Response('<html></html>', { status: 502 }));
    expect(html.code).toBeUndefined();
    expect(html.details).toBeUndefined();
    const str = await streamHttpError(jsonResponse(400, { error: 'plain' }));
    expect(str.code).toBeUndefined();
    expect(str.message).toBe('plain');
  });
});
