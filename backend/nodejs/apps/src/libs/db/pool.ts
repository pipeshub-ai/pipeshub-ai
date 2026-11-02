import { Pool } from 'pg';
import { Logger } from '../services/logger.service';
import { PgPool } from './uow';

export interface PoolOptions {
  connectionString: string;
  /** Names the process in pg_stat_activity: `pipeshub-<service>/<version>`. */
  service: string;
  version: string;
  max?: number;
}

/**
 * The pool runs no reset query on release: the pooler in front resets server sessions, and a client-sent reset
 * would land on whichever server connection is free.
 */
export function createPgPool(o: PoolOptions): Pool & PgPool {
  const pool = new Pool({
    connectionString: o.connectionString,
    max: o.max ?? 10,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
    application_name: `pipeshub-${o.service}/${o.version}`,
  });
  // An idle client that errors would otherwise crash the process.
  pool.on('error', (e) => {
    Logger.getInstance({ service: 'PgPool' }).warn('idle client error', {
      error: e.message,
    });
  });
  return pool;
}
