/**
 * Parse a `Bearer <token>` header value. Returns the token, or null if the
 * header is missing/malformed. Shared between the Express auth middleware
 * (Authorization header) and Socket.IO gateways (handshake `auth.token`).
 */
export function parseBearerToken(
  headerValue: string | undefined | null,
): string | null {
  if (typeof headerValue !== 'string' || headerValue.length === 0) {
    return null;
  }
  const [scheme, token] = headerValue.split(' ');
  if (scheme !== 'Bearer') return null;
  if (typeof token !== 'string' || token.length === 0) return null;
  return token;
}
