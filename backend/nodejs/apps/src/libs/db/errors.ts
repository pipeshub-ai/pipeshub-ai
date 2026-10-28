import { ErrorMetadata } from '../errors/base.error';
import {
  DatabaseError,
  ForeignKeyError,
  UniqueConstraintError,
} from '../errors/database.errors';

/** Typed PostgreSQL failures. `sqlstate` is the server's code when there is one. */
export class PgError extends DatabaseError {
  constructor(
    code: string,
    message: string,
    statusCode: number,
    public readonly sqlstate?: string,
    public readonly cause?: unknown,
    metadata?: ErrorMetadata,
  ) {
    super(code, message, statusCode, metadata);
  }
}

/** Retryable by the unit of work: 40001. */
export class SerializationFailure extends PgError {
  constructor(cause?: unknown) {
    super(
      'SERIALIZATION_FAILURE',
      'serialization failure',
      409,
      '40001',
      cause,
    );
  }
}

/** Retryable by the unit of work: 40P01. */
export class DeadlockDetected extends PgError {
  constructor(cause?: unknown) {
    super('DEADLOCK_DETECTED', 'deadlock detected', 409, '40P01', cause);
  }
}

/** 57014: statement_timeout (or a cancel). Never retried. */
export class DbTimeout extends PgError {
  constructor(cause?: unknown) {
    super('TIMEOUT', 'statement timed out', 504, '57014', cause);
  }
}

/** 55P03: lock_timeout. Never retried. */
export class LockNotAvailable extends PgError {
  constructor(cause?: unknown) {
    super('LOCK_NOT_AVAILABLE', 'lock not available', 409, '55P03', cause);
  }
}

/** 42501: the service role may not do this, or a row-level policy refused a write. A bug, not a user error. */
export class PermissionDenied extends PgError {
  constructor(cause?: unknown) {
    super('PERMISSION_DENIED', 'permission denied', 500, '42501', cause);
  }
}

/** 25P02: a statement ran after an error inside the same transaction (a swallowed error without a savepoint). A bug. */
export class FailedTransaction extends PgError {
  constructor(cause?: unknown) {
    super('FAILED_TRANSACTION', 'transaction is aborted', 500, '25P02', cause);
  }
}

/** 25006: a write in a read-only unit of work, or a write fence that is closed. Retry after the freeze. */
export class DbReadOnly extends PgError {
  constructor(cause?: unknown) {
    super(
      'READ_ONLY',
      'database is read-only for this unit of work',
      503,
      '25006',
      cause,
    );
  }
}

/** 55P04: the write fence says this process's store epoch is stale. */
export class DbFenced extends PgError {
  constructor(cause?: unknown) {
    super(
      'FENCED',
      'store epoch is stale for this process',
      503,
      '55P04',
      cause,
    );
  }
}

/** A column check (23514) failed. */
export class CheckViolation extends PgError {
  constructor(cause?: unknown) {
    super('CHECK_VIOLATION', 'check constraint violated', 400, '23514', cause);
  }
}

/** 53xxx (out of connections, memory or disk) and 25P03 (idle in transaction timeout): the database cannot serve this now. */
export class DbOverloaded extends PgError {
  constructor(sqlstate?: string, cause?: unknown) {
    super('OVERLOADED', 'database is overloaded', 503, sqlstate, cause);
  }
}

/** A value the database cannot store: NUL in text (22021), too long (22001), not null (23502), bad text form (22P02), ... */
export class InvalidData extends PgError {
  constructor(sqlstate: string, cause?: unknown) {
    super('INVALID_DATA', 'a value cannot be stored', 400, sqlstate, cause);
  }
}

/** The connection broke. `commitSent` true means the outcome is unknown and the work was NOT retried. */
export class ConnectionLost extends PgError {
  constructor(
    public readonly commitSent: boolean,
    cause?: unknown,
  ) {
    super('CONNECTION_LOST', 'database connection lost', 503, undefined, cause);
  }
}

/** No pool slot within the pool's connect timeout. */
export class PoolTimeout extends PgError {
  constructor(cause?: unknown) {
    super(
      'POOL_TIMEOUT',
      'timed out waiting for a database connection',
      503,
      undefined,
      cause,
    );
  }
}

/** The prologue (role switch, settings, fence) failed. No caller statement ran. */
export class PrologueFailed extends PgError {
  constructor(cause?: unknown, sqlstate?: string) {
    super(
      'PROLOGUE_FAILED',
      'unit of work prologue failed',
      500,
      sqlstate,
      cause,
    );
  }
}

/** The server connection still had a role set from an earlier session: the pooler did not reset it. */
export class SessionRoleLeak extends PgError {
  constructor(role: string) {
    super(
      'SESSION_ROLE_LEAK',
      'a session role leaked onto this connection',
      500,
      undefined,
      undefined,
      { role },
    );
  }
}

/** A write unit asked for an isolation level above read committed. */
export class WriteIsolationRefused extends PgError {
  constructor(isolation: string) {
    super(
      'WRITE_ISOLATION',
      `write units run at read committed, not ${isolation}`,
      500,
    );
  }
}

/** Programming errors in how the unit of work is used (no query is sent). */
export class UowUsageError extends PgError {
  constructor(message: string) {
    super('UOW_USAGE', message, 500);
  }
}

/** Boot: the schema is older (or newer) than this code accepts. */
export class SchemaNotReady extends PgError {
  constructor(public readonly problems: string[]) {
    super('SCHEMA_NOT_READY', `schema not ready: ${problems.join('; ')}`, 503);
  }
}

const CONNECTION_NODE_CODES = new Set([
  'ECONNRESET',
  'EPIPE',
  'ECONNREFUSED',
  'ETIMEDOUT',
  'ENOTFOUND',
]);

interface PgLike {
  code?: string;
  message?: string;
  constraint?: string;
  detail?: string;
}

export function isConnectionLoss(e: unknown): boolean {
  const { code = '', message = '' } = (e ?? {}) as PgLike;
  return (
    code.startsWith('08') ||
    code === '57P01' || // admin_shutdown
    code === '57P02' || // crash_shutdown
    code === '57P03' || // cannot_connect_now
    CONNECTION_NODE_CODES.has(code) ||
    /connection terminated|not queryable|connection error|server closed the connection/i.test(
      message,
    )
  );
}

export function isRetryable(e: unknown): boolean {
  return e instanceof SerializationFailure || e instanceof DeadlockDetected;
}

/** `Key (org_id, email)=(...) already exists.` -> `org_id,email` */
function keyColumns(detail?: string): string {
  const m = /^Key \(([^)]*)\)=/.exec(detail ?? '');
  return (m?.[1] ?? '').split(', ').join(',');
}

/** Map a driver error to a typed one. Errors that are already typed, and non-database errors, pass through. */
export function mapPgError(e: unknown, commitSent = false): unknown {
  if (e instanceof PgError || !(e instanceof Error)) return e;
  const { code, constraint, detail } = e as PgLike;
  switch (code) {
    case '40001':
      return new SerializationFailure(e);
    case '40P01':
      return new DeadlockDetected(e);
    case '57014':
      return new DbTimeout(e);
    case '55P03':
      return new LockNotAvailable(e);
    case '42501':
      return new PermissionDenied(e);
    case '25P02':
      return new FailedTransaction(e);
    case '25006':
      return new DbReadOnly(e);
    case '55P04':
      return new DbFenced(e);
    case '23514':
      return new CheckViolation(e);
    case '25P03':
      return new DbOverloaded(code, e);
    case '23502':
      return new InvalidData(code, e);
    case '23505':
      // `detail` holds the offending values (emails, names): only the column names are kept, as the Mongo mapping does.
      // On a table with row-level security the server omits `detail`, so the constraint name is the key.
      return new UniqueConstraintError('duplicate key', {
        key: keyColumns(detail) || constraint,
        constraint,
        sqlstate: code,
      });
    case '23503':
      return new ForeignKeyError('foreign key violation', {
        constraint,
        sqlstate: code,
      });
    case undefined:
    default:
      break;
  }
  if (code?.startsWith('53')) return new DbOverloaded(code, e);
  if (code?.startsWith('22')) return new InvalidData(code, e);
  if (isConnectionLoss(e)) return new ConnectionLost(commitSent, e);
  if (/timeout exceeded when trying to connect/i.test(e.message))
    return new PoolTimeout(e);
  return e;
}
