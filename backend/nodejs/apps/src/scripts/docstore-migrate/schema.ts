/**
 * The migrator runs no DDL. "Schema" and "post_load" call the migrate job (the Alembic heads) and check what it left; on
 * compose the one-shot `pipeshub-migrate` service has done the first part before the migrator starts, so the command may be
 * empty and only the checks run.
 */
import { spawn } from 'node:child_process';
import type { MigrationRuntime } from './runtime';
import { redact } from './config';

const psycopg = (u: string): string =>
  u.replace(/^postgres(ql)?:\/\//, 'postgresql+psycopg://');

export async function runMigrateJob(
  rt: MigrationRuntime,
  log: (m: string) => void,
): Promise<boolean> {
  const cmd = rt.cfg.migrateCommand.trim();
  if (!cmd || cmd === 'none') {
    log(
      'schema: no MIGRATE_SCHEMA_COMMAND, the schema is expected to be at head already',
    );
    return false;
  }
  await new Promise<void>((resolve, reject) => {
    const p = spawn('sh', ['-c', cmd], {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PG_MIGRATE_URL: psycopg(rt.cfg.pgMigrateUrl) },
      cwd: process.env.MIGRATE_SCHEMA_CWD,
    });
    let tail = '';
    for (const s of [p.stdout, p.stderr])
      s.on('data', (b: Buffer) => (tail = (tail + b.toString()).slice(-2000)));
    p.on('error', reject);
    p.on('close', (code) => {
      if (code === 0) resolve();
      else
        reject(
          new Error(`migrate job exited with ${String(code)}: ${redact(tail)}`),
        );
    });
  });
  return true;
}

export interface PostLoadReport {
  ok: boolean;
  problems: string[];
  ranMigrateJob: boolean;
}

/** After the copy: apply the post-load heads (when a command is configured) and check the result. */
export async function postLoad(
  rt: MigrationRuntime,
  log: (m: string) => void,
): Promise<PostLoadReport> {
  const problems: string[] = [];
  const ran = await runMigrateJob(rt, log);
  const bad = await rt.migratePool.query<{ conname: string; rel: string }>(
    "SELECT c.conname, c.conrelid::regclass::text AS rel FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE NOT c.convalidated AND n.nspname IN ('identity','conversation','content','oauth','platform','integrations')",
  );
  for (const r of bad.rows)
    problems.push(`constraint ${r.conname} on ${r.rel} is not validated`);
  try {
    const v = await rt.migratePool.query<{ n: string }>(
      'SELECT count(*)::text AS n FROM kernel.rls_violations',
    );
    if (v.rows[0]?.n !== '0')
      problems.push(`kernel.rls_violations holds ${String(v.rows[0]?.n)} rows`);
  } catch (e) {
    problems.push(
      `kernel.rls_violations could not be read: ${(e as Error).message}`,
    );
  }
  return { ok: problems.length === 0, problems, ranMigrateJob: ran };
}
