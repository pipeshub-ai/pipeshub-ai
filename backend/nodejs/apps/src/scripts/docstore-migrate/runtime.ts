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

/** DOC_STORE and the PostgreSQL runtime are process-wide, so one PostgreSQL runtime may be open at a time; this says whether one is. */
let pgOpen = false;

export class MigrationRuntime {
  private closed = false;

  private constructor(
    readonly cfg: MigrateConfig,
    readonly mongo: MongoClient,
    private readonly migrate: Pool | undefined,
    private readonly pools: Array<{ end(): Promise<void> }>,
    /** Set when this runtime switched DOC_STORE: the value to put back on close (`undefined`: it was unset). */
    private readonly docStore?: { previous: string | undefined },
  ) {}

  static async open(cfg: MigrateConfig): Promise<MigrationRuntime> {
    // checked before connecting: a throw after the connection would leave the MongoDB client open and the process hanging
    if (Boolean(cfg.pgImportUrl) !== Boolean(cfg.pgMigrateUrl))
      throw new Error('PG_IMPORT_URL and PG_MIGRATE_URL are both needed');
    if (!cfg.pgImportUrl) return MigrationRuntime.connect(cfg);
    if (pgOpen)
      throw new Error(
        'a PostgreSQL migration runtime is already open in this process; close it first',
      );
    pgOpen = true; // taken before the first await, so two concurrent opens cannot both pass
    try {
      return await MigrationRuntime.connect(cfg);
    } catch (e) {
      pgOpen = false;
      throw e;
    }
  }

  private static async connect(cfg: MigrateConfig): Promise<MigrationRuntime> {
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
    // The repositories are picked by the process-wide DOC_STORE and the copy writes through the PostgreSQL ones. It is switched only
    // here, once everything that can fail has succeeded, and `close()` puts the previous value back.
    const previous = process.env.DOC_STORE;
    process.env.DOC_STORE = 'postgres';
    return new MigrationRuntime(
      cfg,
      mongo,
      migratePool,
      [jobsPool, ...(nodePool ? [nodePool] : []), migratePool],
      { previous },
    );
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
    if (this.closed) return;
    this.closed = true;
    // only the PostgreSQL runtime owns the process-wide settings; a MongoDB-only one must not clear a live one's
    if (this.docStore) {
      configurePg(undefined);
      if (this.docStore.previous === undefined) delete process.env.DOC_STORE;
      else process.env.DOC_STORE = this.docStore.previous;
      pgOpen = false;
    }
    await this.mongo.close().catch(() => undefined);
    for (const p of this.pools) await p.end().catch(() => undefined);
  }
}
