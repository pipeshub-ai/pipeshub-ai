import { Logger } from '../services/logger.service';
import {
  CheckViolation,
  ConnectionLost,
  DbFenced,
  DbOverloaded,
  DbReadOnly,
  InvalidData,
  DbTimeout,
  FailedTransaction,
  LockNotAvailable,
  PermissionDenied,
  PoolTimeout,
  Tx as PgKernelTx,
  PgRuntime,
  mapPgError,
  withUow,
} from '../db';
import {
  GatewayTimeoutError,
  InternalServerError,
  ServiceUnavailableError,
} from '../errors/http.errors';
import { ValidationError } from '../errors/validation.error';
import {
  ConflictError,
  DocStoreFencedError,
  DocStoreReadOnlyError,
  VersionConflictError,
} from './errors';
import { docstoreMetrics } from './metrics';
import type { Tx, UnitOfWork } from './uow';

const logger = Logger.getInstance({ service: 'PgDocStore' });

export class PgTx implements Tx {
  readonly store = 'postgres' as const;
  constructor(readonly kernel: PgKernelTx) {}
}

/** The PostgreSQL unit of work behind a `Tx`, for PostgreSQL repositories only. */
export function kernelTx(tx: Tx): PgKernelTx {
  // a mixed-store unit (testing aid, see mixed.ts) carries its PostgreSQL unit beside the Mongo one
  const pg = tx instanceof PgTx ? tx : (tx as { pgTx?: PgTx }).pgTx;
  if (!pg)
    throw new InternalServerError(
      'a PostgreSQL repository was given a transaction from another store',
    );
  return pg.kernel;
}

/**
 * The seam's typed error for a PostgreSQL failure ; anything else is returned unchanged.
 * Unique and foreign-key violations are already `UniqueConstraintError{key}` / `ForeignKeyError` from `mapPgError`.
 */
export function mapPgStoreError(error: unknown): unknown {
  const e = mapPgError(error);
  if (e instanceof DbReadOnly) {
    docstoreMetrics.fenceRefused.inc({ store: 'postgres', reason: 'readonly' });
    return new DocStoreReadOnlyError();
  }
  if (e instanceof DbFenced) {
    docstoreMetrics.fenceRefused.inc({ store: 'postgres', reason: 'epoch' });
    return new DocStoreFencedError();
  }
  if (e instanceof FailedTransaction) {
    // a statement ran after a swallowed error without a savepoint: a bug, never a user error
    docstoreMetrics.failedTransaction.inc();
    logger.error('statement after an error inside a unit of work');
    return new InternalServerError('Database transaction was aborted');
  }
  if (e instanceof PermissionDenied) {
    docstoreMetrics.rlsViolation.inc();
    logger.error('privilege or row policy refused a write (wrong org?)');
    return new InternalServerError('Database refused the operation');
  }
  if (e instanceof DbTimeout)
    return new GatewayTimeoutError('Database timed out');
  if (e instanceof LockNotAvailable)
    return new ConflictError('Another change holds this record; try again');
  if (e instanceof CheckViolation)
    return new ValidationError('A value broke a database rule', []);
  if (e instanceof InvalidData)
    return new ValidationError('A value cannot be stored', []);
  if (
    e instanceof ConnectionLost ||
    e instanceof PoolTimeout ||
    e instanceof DbOverloaded
  )
    return new ServiceUnavailableError('Database is unavailable');
  return e;
}

/** A guarded `UPDATE ... WHERE version = $n` that matched no row: the same error Mongoose's VersionError becomes. */
export function assertVersionMatched(
  rowCount: number | null,
  what: string,
): void {
  if (rowCount !== 1)
    throw new VersionConflictError(`${what} was modified concurrently`);
}

/**
 * One transaction on the kernel's unit of work (`withUow`): org from the ambient OrgContext, write units carry the
 * store fence, 40001/40P01 re-run `fn`. `fn` therefore must not have side effects outside `tx`; the lint in
 * tests/libs/repositories/pg-lints.test.ts keeps network calls out of it.
 */
export class PgUnitOfWork implements UnitOfWork {
  constructor(private readonly runtime?: PgRuntime) {}

  async run<T>(
    fn: (tx: Tx) => Promise<T>,
    opts?: { readOnly?: boolean; preOrg?: boolean; timeoutMs?: number },
  ): Promise<T> {
    const o = {
      write: !opts?.readOnly,
      preOrg: opts?.preOrg === true,
      ...(opts?.timeoutMs !== undefined && { timeoutMs: opts.timeoutMs }),
    };
    const body = (kt: PgKernelTx): Promise<T> => fn(new PgTx(kt));
    try {
      return await (this.runtime
        ? this.runtime.withUow(o, body)
        : withUow(o, body));
    } catch (error) {
      throw mapPgStoreError(error);
    }
  }
}
