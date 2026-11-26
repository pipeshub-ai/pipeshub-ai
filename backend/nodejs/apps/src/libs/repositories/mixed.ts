/**
 * Testing aid while repositories are ported (`DOC_STORE=postgres` with `DOC_STORE_MONGO_REPOS`): a unit of work for code
 * that touches repositories of both stores. It opens the Mongo unit and, when an organisation is bound, the PostgreSQL
 * one, and hands repositories a `Tx` that carries both. The two commit independently, so this is not atomic across
 * stores: never use it for anything but running a half-ported install.
 */
import { getCurrentOrgId } from '../context/orgContext';
import type { PgTx } from './pg';
import type { MongoTx } from './mongo';
import type { Tx, UnitOfWork } from './uow';

export class MixedTx implements Tx {
  readonly store = 'postgres' as const;
  constructor(
    readonly session?: MongoTx['session'],
    readonly pgTx?: PgTx,
  ) {}
}

export class MixedUnitOfWork implements UnitOfWork {
  constructor(
    private readonly mongo: UnitOfWork,
    private readonly pg: UnitOfWork,
  ) {}

  run<T>(
    fn: (tx: Tx) => Promise<T>,
    opts?: { readOnly?: boolean },
  ): Promise<T> {
    return this.mongo.run((m) => {
      const session = (m as MongoTx).session;
      // before an organisation exists (sign-up) only Mongo repositories can be in play
      if (!getCurrentOrgId()) return fn(new MixedTx(session));
      return this.pg.run((p) => fn(new MixedTx(session, p as PgTx)), opts);
    }, opts);
  }
}
