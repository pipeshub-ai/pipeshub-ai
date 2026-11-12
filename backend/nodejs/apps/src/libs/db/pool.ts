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

const LOCAL_HOSTS = ['localhost', '127.0.0.1', '[::1]', '::1'];

/** A warning (never the URL) when a non-local PostgreSQL URL may connect without TLS. */
export function plaintextRemoteWarning(
  service: string,
  url: string,
): string | undefined {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return undefined;
  }
  const host = (u.searchParams.get('host') ?? u.hostname).toLowerCase();
  if (host === '' || host.startsWith('/') || LOCAL_HOSTS.includes(host))
    return undefined;
  const mode = u.searchParams.get('sslmode') ?? 'prefer';
  if (!['disable', 'allow', 'prefer'].includes(mode)) return undefined;
  return `PostgreSQL URL of service "${service}" points at a non-local host without TLS; add ?sslmode=verify-full (at least require)`;
}

/**
 * The pool runs no reset query on release: the pooler in front resets server sessions, and a client-sent reset
 * would land on whichever server connection is free.
 */
export function createPgPool(o: PoolOptions): Pool & PgPool {
  const warning = plaintextRemoteWarning(o.service, o.connectionString);
  if (warning !== undefined)
    Logger.getInstance({ service: 'PgPool' }).warn(warning);
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
