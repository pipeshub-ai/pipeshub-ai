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
