import type { Tx, UnitOfWork } from '../../../src/libs/repositories/uow';

/**
 * A unit of work that can be told which organisation it runs for. The contract suites are written against any store:
 * a store whose units run as an organisation (PostgreSQL) needs it, one that does not (Mongo) ignores it.
 */
export interface ScopedUnitOfWork extends Omit<UnitOfWork, 'run'> {
  run<T>(fn: (tx: Tx) => Promise<T>, opts?: { readOnly?: boolean; org?: string; user?: string }): Promise<T>;
}
