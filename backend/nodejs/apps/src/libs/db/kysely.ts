import {
  CompiledQuery,
  DatabaseConnection,
  Dialect,
  Driver,
  Kysely,
  PostgresAdapter,
  PostgresIntrospector,
  PostgresQueryCompiler,
  QueryResult,
} from 'kysely';
import { UowUsageError } from './errors';

type Exec = (
  text: string,
  values: readonly unknown[],
) => Promise<{ rows: unknown[]; rowCount: number | null }>;

/** Kysely over a statement executor. It never opens, begins or commits anything: the unit of work owns that. */
export function kyselyOn<DB>(exec: Exec): Kysely<DB> {
  const connection: DatabaseConnection = {
    async executeQuery<R>(q: CompiledQuery): Promise<QueryResult<R>> {
      const r = await exec(q.sql, q.parameters);
      return {
        rows: r.rows as R[],
        numAffectedRows: r.rowCount == null ? undefined : BigInt(r.rowCount),
      };
    },
    streamQuery() {
      throw new UowUsageError('streaming cursors are not available');
    },
  };
  const driver: Driver = {
    init: async () => {},
    acquireConnection: () => Promise.resolve(connection),
    beginTransaction: () =>
      Promise.reject(new UowUsageError('use withUow for transactions')),
    commitTransaction: () =>
      Promise.reject(new UowUsageError('use withUow for transactions')),
    rollbackTransaction: () =>
      Promise.reject(new UowUsageError('use withUow for transactions')),
    releaseConnection: async () => {},
    destroy: async () => {},
  };
  const dialect: Dialect = {
    createAdapter: () => new PostgresAdapter(),
    createDriver: () => driver,
    createIntrospector: (db) => new PostgresIntrospector(db),
    createQueryCompiler: () => new PostgresQueryCompiler(),
  };
  return new Kysely<DB>({ dialect });
}
