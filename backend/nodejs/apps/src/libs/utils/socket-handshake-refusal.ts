/**
 * Errors a Socket.IO handshake middleware passes to `next()`. Socket.IO sends
 * the error's `message` and `data` to the client (`connect_error`), so `data`
 * tells clients whether trying again later can help without matching on the
 * message text.
 */
export const HANDSHAKE_REFUSAL = {
  TOKEN_MISSING: 'AUTH_TOKEN_MISSING',
  TOKEN_INVALID: 'AUTH_TOKEN_INVALID',
  SESSION_REFUSED: 'AUTH_SESSION_REFUSED',
  AUTH_FAILED: 'AUTH_FAILED',
  SESSION_CHECK_UNAVAILABLE: 'SESSION_CHECK_UNAVAILABLE',
} as const;

export type HandshakeRefusalCode =
  (typeof HANDSHAKE_REFUSAL)[keyof typeof HANDSHAKE_REFUSAL];

export interface HandshakeRefusalData {
  code: HandshakeRefusalCode;
  /** True only when the same handshake may succeed later without signing in again. */
  retryable: boolean;
}

export type HandshakeRefusal<E extends Error = Error> = E & {
  data: HandshakeRefusalData;
};

/** An auth refusal: retrying the same token cannot help. */
export function authRefusal<E extends Error>(
  error: E,
  code: Exclude<HandshakeRefusalCode, 'SESSION_CHECK_UNAVAILABLE'>,
): HandshakeRefusal<E> {
  return Object.assign(error, { data: { code, retryable: false } });
}

/**
 * The session could not be checked (database unreachable): fail closed, but
 * not as an auth failure, so clients try again later instead of signing out.
 */
export function sessionCheckUnavailable(): HandshakeRefusal {
  return Object.assign(new Error('Unable to check the session right now'), {
    data: {
      code: HANDSHAKE_REFUSAL.SESSION_CHECK_UNAVAILABLE,
      retryable: true,
    },
  });
}
