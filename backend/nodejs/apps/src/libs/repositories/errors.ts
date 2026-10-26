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
