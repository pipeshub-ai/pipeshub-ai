import { createPgPool } from './pool';
import { PgRuntime, configurePg, pgRuntime } from './uow';

/**
 * The runtime for the platform's own tables (key-value entries, the work queue). When the document store has
 * already configured PostgreSQL for this process that runtime is used; otherwise one is built from `PG_NODE_URL`,
 * without the store fence (these tables are not document-store data). Safe to call many times.
 */
export function ensurePgRuntime(version = 'runtime'): PgRuntime {
  try {
    return pgRuntime();
  } catch {
    const url = process.env.PG_NODE_URL;
    if (!url)
      throw new Error(
        'KV_STORE_TYPE=postgres and MESSAGE_BROKER=postgres need PG_NODE_URL',
      );
    const rt = new PgRuntime({
      pool: createPgPool({
        connectionString: url,
        service: 'node',
        version,
        max: Number(process.env.PG_NODE_POOL_MAX ?? 10),
      }),
      defaultService: 'node',
      pooler:
        (process.env.PG_POOLER as 'pgbouncer_txn' | 'none' | undefined) ??
        'pgbouncer_txn',
    });
    configurePg(rt);
    return rt;
  }
}
