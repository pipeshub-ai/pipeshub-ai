/** Where the migrator connects. URLs hold passwords: never print them (`redact`). */
export interface MigrateConfig {
  mongoUri: string;
  mongoDb: string;
  /** Database of the Slack bot when it is not the API's. */
  slackDb?: string;
  /** The import login (`pipeshub_import`): writes the tables the copy fills and saves the job's progress. It is not the jobs login the
   * application's background processes hold, and it can log in only while a move is running. Empty for commands that only touch MongoDB (drain). */
  pgImportUrl: string;
  /** The migrate login: starts and ends the job, reads job state and the global tables, runs the schema heads. */
  pgMigrateUrl: string;
  /** The node login: only for what must run as an organisation (claiming a slug, the isolation probe). Optional. */
  pgNodeUrl?: string;
  /** Directory the target data volume lives on, for the disk check (preflight). */
  pgDataDir?: string;
  /** Bytes free on the target, when the operator knows better than the check can (tests). */
  pgFreeBytes?: number;
  batchSize: number;
  batchBytes: number;
  /** Workers copying one big collection in parallel, each on its own `_id` range (1: sequential). */
  parallel: number;
  /** A collection is split only above this many documents. */
  parallelMinDocs: number;
  /** Lag of the follower above which the alarm is raised. */
  lagAlarmMs: number;
  /** Wait after a forced fence for a connected application (`--allow-clients`); unset: the write deadline plus the trust window. */
  writerGraceMs?: number;
  /** Test aid: pause after every bulk batch. */
  batchDelayMs: number;
  /** Command that applies Alembic heads, e.g. `python -m db.migrate`; run with PG_MIGRATE_URL in its environment. */
  migrateCommand: string;
}

const need = (env: NodeJS.ProcessEnv, k: string): string => {
  const v = env[k];
  if (!v) throw new Error(`${k} is not set`);
  return v;
};

/** psycopg-style URLs (`postgresql+psycopg://`) as `pg` reads them. */
export const pgUrl = (u: string): string =>
  u.replace(/^postgres(ql)?\+[a-z0-9]+:\/\//, 'postgresql://');

export function loadConfig(
  env: NodeJS.ProcessEnv = process.env,
): MigrateConfig {
  return {
    mongoUri: env.MONGO_URI ?? env.MONGO_URL ?? need(env, 'MONGO_URI'),
    mongoDb: env.MONGO_DB_NAME ?? 'es',
    ...(env.MONGO_SLACK_DB_NAME ? { slackDb: env.MONGO_SLACK_DB_NAME } : {}),
    pgImportUrl: env.PG_IMPORT_URL ? pgUrl(env.PG_IMPORT_URL) : '',
    pgMigrateUrl: env.PG_MIGRATE_URL ? pgUrl(env.PG_MIGRATE_URL) : '',
    ...(env.PG_NODE_URL ? { pgNodeUrl: pgUrl(env.PG_NODE_URL) } : {}),
    ...(env.PG_DATA_DIR ? { pgDataDir: env.PG_DATA_DIR } : {}),
    ...(env.MIGRATE_PG_FREE_BYTES
      ? { pgFreeBytes: Number(env.MIGRATE_PG_FREE_BYTES) }
      : {}),
    batchSize: Number(env.MIGRATE_BATCH_SIZE ?? 1000),
    batchBytes: Number(env.MIGRATE_BATCH_BYTES ?? 8 * 1024 * 1024),
    parallel: Math.max(1, Number(env.MIGRATE_PARALLEL ?? 4)),
    parallelMinDocs: Number(env.MIGRATE_PARALLEL_MIN_DOCS ?? 50_000),
    lagAlarmMs: Number(env.MIGRATE_LAG_ALARM_MS ?? 60_000),
    ...(env.MIGRATE_WRITER_GRACE_MS
      ? { writerGraceMs: Number(env.MIGRATE_WRITER_GRACE_MS) }
      : {}),
    batchDelayMs: Number(env.MIGRATE_BATCH_DELAY_MS ?? 0),
    migrateCommand: env.MIGRATE_SCHEMA_COMMAND ?? 'python -m db.migrate',
  };
}

/** A URL or message with any password removed (through the last `@` before the first `/`, `?` or `#`, so a password holding an `@` is covered and a query string is not). */
export const redact = (s: string): string =>
  s.replace(/(:\/\/[^:/@\s]+:)[^/?#\s]*@/g, '$1***@');
