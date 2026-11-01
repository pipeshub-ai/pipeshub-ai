/** The connections a migration run holds and the units of work it opens on them. */
import { Pool } from 'pg';
import { MongoClient, Db } from 'mongodb';
import {
  PgRuntime,
  createPgPool,
  configurePg,
  withJobsUow,
  withUow,
} from '../../libs/db';
import type { Tx as KernelTx } from '../../libs/db';
import { forOrg, system } from '../../libs/context/orgContext';
import { PgTx } from '../../libs/repositories/pg';
import type { Tx } from '../../libs/repositories/uow';
import type { MigrateConfig } from './config';

/** The migrate login's pool. An idle client error (a restart, a pooler disconnect) must not end a long run: the next query reconnects. */
export function createMigratePool(connectionString: string): Pool {
  const pool = new Pool({
    connectionString,
    max: 2,
    application_name: 'docstore-migrate',
  });
  pool.on('error', (e) => {
    console.warn(`migrate pool idle client error: ${e.message}`);
  });
  return pool;
}

export class MigrationRuntime {
  private constructor(
    readonly cfg: MigrateConfig,
    readonly mongo: MongoClient,
    private readonly migrate: Pool | undefined,
    private readonly pools: Array<{ end(): Promise<void> }>,
  ) {}

  static async open(cfg: MigrateConfig): Promise<MigrationRuntime> {
    // checked before connecting: a throw after the connection would leave the MongoDB client open and the process hanging
    if (Boolean(cfg.pgImportUrl) !== Boolean(cfg.pgMigrateUrl))
      throw new Error('PG_IMPORT_URL and PG_MIGRATE_URL are both needed');
    // set only once the configuration is accepted, so a refused open leaves the process as it was
    // the repositories are picked by DOC_STORE; the copy writes through the PostgreSQL ones
    process.env.DOC_STORE = 'postgres';
    const mongo = await new MongoClient(cfg.mongoUri, {
      serverSelectionTimeoutMS: 8000,
      appName: 'docstore-migrate',
      retryWrites: false,
    }).connect();
    if (!cfg.pgImportUrl && !cfg.pgMigrateUrl)
      return new MigrationRuntime(cfg, mongo, undefined, []);
    const jobsPool = createPgPool({
      connectionString: cfg.pgImportUrl,
      service: 'import',
      version: 'migrate',
      max: cfg.parallel + 2,
    });
    const nodePool = cfg.pgNodeUrl
      ? createPgPool({
          connectionString: cfg.pgNodeUrl,
          service: 'node',
          version: 'migrate',
          max: 4,
        })
      : undefined;
    const migratePool = createMigratePool(cfg.pgMigrateUrl);
    // no store fence: the migrator is the one that moves it
    configurePg(
      new PgRuntime({
        pool: nodePool ?? jobsPool,
        jobsPool,
        defaultService: nodePool ? 'node' : 'jobs',
        pooler:
          (process.env.PG_POOLER as 'pgbouncer_txn' | undefined) ??
          'pgbouncer_txn',
      }),
    );
    return new MigrationRuntime(cfg, mongo, migratePool, [
      jobsPool,
      ...(nodePool ? [nodePool] : []),
      migratePool,
    ]);
  }

  /** The migrate login (job state, global tables). */
  get migratePool(): Pool {
    if (!this.migrate)
      throw new Error('PG_MIGRATE_URL and PG_IMPORT_URL are not set');
    return this.migrate;
  }

  db(name?: string): Db {
    return this.mongo.db(name ?? this.cfg.mongoDb);
  }

  /** A write unit of the jobs role (no organisation): `tx` goes to the repositories, `k` to plain statements. */
  jobs<T>(
    fn: (tx: Tx, k: KernelTx) => Promise<T>,
    opts: { write?: boolean } = {},
  ): Promise<T> {
    return system('docstore-migrate', () =>
      withJobsUow({ write: opts.write !== false, service: 'import' }, (k) =>
        fn(new PgTx(k), k),
      ),
    );
  }

  /** A unit of the node login that runs as `orgId` (claiming a slug, probes). Needs `PG_NODE_URL`. */
  asOrg<T>(
    orgId: string,
    fn: (tx: Tx, k: KernelTx) => Promise<T>,
    opts: { write?: boolean } = {},
  ): Promise<T> {
    if (!this.cfg.pgNodeUrl) throw new Error('PG_NODE_URL is not set');
    return forOrg(orgId, 'docstore-migrate', () =>
      withUow({ write: opts.write !== false }, (k) => fn(new PgTx(k), k)),
    );
  }

  async close(): Promise<void> {
    configurePg(undefined);
    await this.mongo.close().catch(() => undefined);
    for (const p of this.pools) await p.end().catch(() => undefined);
  }
}
