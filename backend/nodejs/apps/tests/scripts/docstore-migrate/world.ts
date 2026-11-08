/** A real MongoDB replica set and PostgreSQL (behind PgBouncer) with a migration runtime open on them, for the suites that run whole steps. */
import { PgStack, startPgStack } from '../../stores/pg-store-harness';
import { loadConfig } from '../../../src/scripts/docstore-migrate/config';
import { MigrationRuntime } from '../../../src/scripts/docstore-migrate/runtime';
import { TestMongo, startMongo } from './mongo-harness';

/** Schemas the copy fills; the three tables here belong to the install and are kept. */
const TABLE_SCHEMAS = ['identity', 'conversation', 'content', 'oauth', 'platform', 'integrations'];
const KEEP = new Set(['platform.store_epoch', 'platform.deployment_profile', 'platform.consumer_inbox']);

/** MongoDB refuses U+0000 in a key, so fixture keys that have one are written without it. */
const mongoSafe = (v: unknown): unknown => {
  if (Array.isArray(v)) return v.map(mongoSafe);
  if (v && typeof v === 'object' && Object.getPrototypeOf(v) === Object.prototype)
    return Object.fromEntries(Object.entries(v as object).map(([k, x]) => [k.split('\u0000').join(''), mongoSafe(x)]));
  return v;
};

export interface World {
  stack: PgStack;
  mongo: TestMongo;
  db: string;
  env: NodeJS.ProcessEnv;
  rt: MigrationRuntime;
  /** Drops the MongoDB database and writes `docs` into it. */
  reseed(docs: Record<string, unknown[]>): Promise<void>;
  /** Empties every table the copy fills and every job. */
  truncate(): Promise<void>;
  stop(): Promise<void>;
}

export async function startWorld(bin: string, pgDb: string, mongoDb: string, extraEnv: NodeJS.ProcessEnv = {}, replSet = true): Promise<World> {
  process.env.DOC_STORE = 'postgres';
  const stack = await startPgStack(bin, pgDb, { importer: true });
  const mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp', replSet);
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    MONGO_URI: mongo.uri,
    MONGO_DB_NAME: mongoDb,
    PG_IMPORT_URL: stack.cluster.url(pgDb, 'pipeshub_import').replace('pipeshub_import@', 'pipeshub_import:x@'), // trust auth ignores it; an empty password reads as a shut login
    PG_NODE_URL: stack.cluster.url(pgDb, 'pipeshub_node_login', stack.bouncer.port),
    PG_MIGRATE_URL: stack.cluster.url(pgDb, 'pipeshub_migrate'),
    MIGRATE_SCHEMA_COMMAND: 'none',
    MIGRATE_BATCH_SIZE: '2',
    MIGRATE_PARALLEL: '1',
    MIGRATE_PG_FREE_BYTES: String(10 ** 12),
    ...extraEnv,
  };
  const rt = await MigrationRuntime.open(loadConfig(env));
  return {
    stack,
    mongo,
    db: mongoDb,
    env,
    rt,
    async reseed(docs) {
      await mongo.client.db(mongoDb).dropDatabase();
      for (const [name, list] of Object.entries(docs))
        if (list.length) await mongo.client.db(mongoDb).collection(name).insertMany(list.map(mongoSafe) as never[], { ordered: false });
    },
    async truncate() {
      await stack.admin(async (c) => {
        const t = await c.query(
          `SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r','p') AND NOT c.relispartition AND n.nspname = ANY($1)`,
          [TABLE_SCHEMAS],
        );
        const names = t.rows.map((r: { t: string }) => r.t).filter((n: string) => !KEEP.has(n) && n !== 'platform.jobs');
        await c.query(`TRUNCATE ${names.join(', ')} RESTART IDENTITY CASCADE`);
        await c.query('DELETE FROM platform.jobs');
        await c.query('ALTER SEQUENCE identity.org_slug_seq RESTART WITH 1001');
      });
    },
    async stop() {
      await this.rt.close(); // the current one: a suite that ran a command reopened it
      await mongo.stop();
      await stack.stop();
    },
  };
}
