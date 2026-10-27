import { describe, it, expect } from 'vitest';
import { ErrorType } from '@/lib/api/api-error';
import { isRejectedCursorError, quietRejectedCursor } from '../cursor-recovery';

const CURSOR = 'signed.cursor.token';

/** The shape the axios interceptor hands components. */
function processed(statusCode: number, type: ErrorType) {
  return { type, message: 'boom', statusCode };
}

describe('isRejectedCursorError', () => {
  it('reports a 400 raised while a cursor was in play', () => {
    expect(isRejectedCursorError(processed(400, ErrorType.VALIDATION_ERROR), CURSOR)).toBe(true);
  });

  it('reports a 400 from a raw axios error too', () => {
    // Not every call site receives an already-processed error.
    expect(isRejectedCursorError({ response: { status: 400 } }, CURSOR)).toBe(true);
  });

  it('ignores a 400 when no cursor was sent', () => {
    // Nothing to fall back from: the first page is already the first page.
    expect(isRejectedCursorError(processed(400, ErrorType.VALIDATION_ERROR), null)).toBe(false);
  });

  it.each([
    [403, ErrorType.AUTHORIZATION_ERROR],
    [404, ErrorType.NOT_FOUND],
    [500, ErrorType.SERVER_ERROR],
  ])('leaves a %i to its own handler', (status, type) => {
    // These are about the node or the server; silently resetting the page
    // would hide an access change or an outage behind an empty first page.
    expect(isRejectedCursorError(processed(status, type), CURSOR)).toBe(false);
  });

  it('ignores a network error with no status', () => {
    expect(isRejectedCursorError({ type: ErrorType.NETWORK_ERROR, message: 'offline' }, CURSOR)).toBe(
      false
    );
  });
});

describe('quietRejectedCursor', () => {
  it('keeps the error toast off for the refusal the page recovers from', () => {
    expect(quietRejectedCursor(CURSOR)(processed(400, ErrorType.VALIDATION_ERROR))).toBe(true);
  });

  it('lets every other failure reach the toast', () => {
    expect(quietRejectedCursor(CURSOR)(processed(500, ErrorType.SERVER_ERROR))).toBe(false);
    expect(quietRejectedCursor(undefined)(processed(400, ErrorType.VALIDATION_ERROR))).toBe(false);
  });
});
