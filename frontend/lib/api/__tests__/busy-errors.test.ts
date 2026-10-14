import { describe, it, expect } from 'vitest';
import { AxiosError, AxiosHeaders } from 'axios';
import { busyMessage, parseRetryAfter, processError } from '../api-error';

function httpError(status: number, data: unknown, headers: Record<string, string> = {}) {
  const error = new AxiosError(`Request failed with status code ${status}`);
  error.response = {
    status,
    statusText: '',
    data,
    headers: new AxiosHeaders(headers),
    config: { headers: new AxiosHeaders() },
  } as never;
  return error as AxiosError<never>;
}

describe('busy and slow responses', () => {
  it.each([429, 503, 504])('%i with no message reads as busy, never as axios text', (status) => {
    const processed = processError(httpError(status, {}));
    expect(processed.message).toBe(busyMessage());
    expect(processed.message).not.toMatch(/status code/);
  });

  it('uses the Retry-After seconds when the server sends them', () => {
    const processed = processError(httpError(503, {}, { 'retry-after': '5' }));
    expect(processed.message).toBe('PipesHub is busy right now. Please try again in 5 seconds.');
  });

  it("prefers the server's own words", () => {
    const processed = processError(
      httpError(503, { error: { message: "We couldn't confirm your sign-in just now." } }),
    );
    expect(processed.message).toBe("We couldn't confirm your sign-in just now.");
  });
});

describe('Retry-After parsing', () => {
  const now = Date.parse('Wed, 21 Oct 2026 07:28:00 GMT');

  it('reads whole seconds and a future HTTP date', () => {
    expect(parseRetryAfter('7', now)).toBe(7);
    expect(parseRetryAfter('Wed, 21 Oct 2026 07:28:04 GMT', now)).toBe(4);
  });

  it('ignores a past date, garbage and waits too long to quote', () => {
    expect(parseRetryAfter('Wed, 21 Oct 2026 07:27:00 GMT', now)).toBeUndefined();
    expect(parseRetryAfter('soon', now)).toBeUndefined();
    expect(parseRetryAfter('600', now)).toBeUndefined();
    expect(parseRetryAfter('600', now, Infinity)).toBe(600);
  });

  it('quotes the seconds left when a busy response sends a date', () => {
    const inFive = new Date(Date.now() + 4_500).toUTCString();
    const processed = processError(httpError(503, {}, { 'retry-after': inFive }));
    expect(processed.message).toMatch(/^PipesHub is busy right now\. Please try again in [45] seconds\.$/);
  });
});
