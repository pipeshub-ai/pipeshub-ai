/**
 * Reset link repository: password-reset links that have been used.
 *
 * Writing the row is how a request claims the link: a unique key lets exactly
 * one of two simultaneous requests in, where comparing against the
 * PASSWORD_CHANGED activity cannot, because that is written only once the
 * reset has finished.
 */
import { createHash } from 'crypto';

/** The store could not guarantee the single-use check (its unique key is not built). */
export class ResetLinkStoreNotReadyError extends Error {
  constructor(message = 'Used-link store is not ready') {
    super(message);
    this.name = 'ResetLinkStoreNotReadyError';
  }
}

/** Stored instead of the link itself, which would still work until it expires. */
export function hashResetLink(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

export interface ResetLinkRepository {
  /**
   * Marks the link used. Returns false when it already was. Throws
   * `ResetLinkStoreNotReadyError` when the single-use check cannot be relied on.
   */
  claim(input: {
    linkHash: string;
    userId: string;
    orgId: string;
    expiresAt: Date;
  }): Promise<boolean>;
  /** Hands an unused link back (the reset failed before the password changed). */
  release(linkHash: string): Promise<void>;
}
