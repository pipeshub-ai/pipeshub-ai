/**
 * Store-neutral errors a repository throws, so callers never test a driver's
 * error codes. The classes already exist in libs/errors; this file only adds
 * the one that was missing and gives repositories a single import.
 *
 * Status codes are the ones a caller sees today if it does not handle the error:
 * a unique-key violation is 409, a version conflict is a plain 500 (mongoose's
 * VersionError was never mapped). A caller that answers differently, e.g. 400
 * for a duplicate group name, catches UniqueConstraintError and does so itself.
 */
import { DatabaseError } from '../errors/database.errors';
import {
  GatewayTimeoutError,
  ServiceUnavailableError,
} from '../errors/http.errors';
import type { ErrorMetadata } from '../errors/base.error';

export {
  UniqueConstraintError,
  ForeignKeyError,
} from '../errors/database.errors';
export { ConflictError, NotFoundError } from '../errors/http.errors';

/** A guarded update found the row changed since it was read. */
export class VersionConflictError extends DatabaseError {
  constructor(message: string, metadata?: ErrorMetadata) {
    super('VERSION_CONFLICT', message, 500, metadata);
  }
}

/** The store is frozen for a cutover: retry after it (HTTP 503 with a Retry-After header; consumers re-throw so the message is redelivered). */
export class DocStoreReadOnlyError extends ServiceUnavailableError {
  constructor(retryAfterSeconds = 5) {
    // `retryAfter` (a string) is the field the error middleware turns into a Retry-After header
    super('The document store is read-only for a moment', {
      retryAfter: String(retryAfterSeconds),
    });
  }
}

/** This process was started for a store that is no longer the live one: it must restart on the new profile. */
export class DocStoreFencedError extends ServiceUnavailableError {
  constructor(reason = 'store epoch is stale for this process') {
    super(`The document store refused this write: ${reason}`);
  }
}

/**
 * A store failure that passes by itself (frozen for a cutover, a stale process, unreachable, timed out): a consumer re-throws it
 * so the message is delivered again instead of being committed as handled.
 */
export const isRetryableStoreError = (e: unknown): boolean =>
  e instanceof ServiceUnavailableError || e instanceof GatewayTimeoutError;
