import { createHmac } from 'node:crypto';

const USER_ACTION_KEY_CONTEXT = 'pipeshub/jwt/user-action/v1';

/**
 * Key for tokens handed to end users (reset links, refresh tokens). Services
 * that hold only the raw scoped secret cannot verify tokens signed with it.
 */
export function deriveUserActionSecret(scopedJwtSecret: string): string {
  // An HMAC keyed with '' is a public constant, so anyone could sign with it.
  if (!scopedJwtSecret) {
    throw new Error('Scoped JWT secret is not configured');
  }
  return createHmac('sha256', scopedJwtSecret)
    .update(USER_ACTION_KEY_CONTEXT)
    .digest('hex');
}
