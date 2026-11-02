import { ChildProcess, execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { join } from 'node:path'
import { Client } from 'pg'

/**
 * Real PostgreSQL clusters and PgBouncers for the integration tests. Configured by environment:
 *   PG_TEST_BINS       comma-separated PostgreSQL bin directories (one cluster per entry)
 *   PG_TEST_PGBOUNCER     the pgbouncer binary
 *   PG_TEST_WORK          a scratch directory (default: os tmp)
 *   PG_TEST_MIGRATE_PYTHON    python with the migration tool's requirements (the kernel is migrated for every test database)
 * Without all three the integration tests skip.
 */
export const PG_BINS = (process.env.PG_TEST_BINS ?? '').split(',').filter(Boolean)
export const PGBOUNCER = process.env.PG_TEST_PGBOUNCER ?? ''
export const MIGRATE_PY = process.env.PG_TEST_MIGRATE_PYTHON ?? ''
export const haveStack = PG_BINS.length > 0 && existsSync(PGBOUNCER) && MIGRATE_PY !== ''

export const KERNEL_RESET =
  'RESET ALL; SET SESSION AUTHORIZATION DEFAULT; CLOSE ALL; DISCARD TEMP; UNLISTEN *; SELECT pg_advisory_unlock_all()'

export function freePort(start: number): Promise<number> {
  const probe = (p: number): Promise<number> =>
    new Promise((res) => {
      const s = createServer()
      s.once('error', () => res(probe(p + 1)))
      s.listen(p, '127.0.0.1', () => s.close(() => res(p)))
    })
  return probe(start)
}

export class Cluster {
  constructor(readonly bin: string, readonly root: string, readonly port: number, readonly major: number) {}

  async admin<T>(fn: (c: Client) => Promise<T>, db = 'postgres'): Promise<T> {
    const c = new Client({ host: '127.0.0.1', port: this.port, user: 'postgres', database: db })
    await c.connect()
    try {
      return await fn(c)
    } finally {
      await c.end()
    }
  }

  url(db: string, user: string, port = this.port): string {
    return `postgresql://${user}@127.0.0.1:${port}/${db}`
  }

  stop(): void {
    try {
      execFileSync(join(this.bin, 'pg_ctl'), ['-D', join(this.root, 'data'), '-m', 'fast', '-t', '180', 'stop'], { stdio: ['ignore', 'pipe', 'pipe'] })
    } finally {
      rmSync(this.root, { recursive: true, force: true })
    }
  }
}

/** Where test servers look for a free port: `PG_TEST_PORT_BASE` and `PG_TEST_PORT_SPAN` keep a run inside a range its host has set aside. */
const PORT_BASE = Number(process.env.PG_TEST_PORT_BASE ?? 20000)
const PORT_SPAN = Number(process.env.PG_TEST_PORT_SPAN ?? 30000)

/**
 * Ports are probed by binding, and another process can take one between the probe and the server's own bind, so a
 * start that fails is retried on a fresh random port.
 */
async function withFreePort<T>(attempt: (port: number) => Promise<T>, base: number, span: number): Promise<T> {
  let last: unknown
  for (let i = 0; i < 8; i++) {
    try {
      return await attempt(await freePort(base + Math.floor(Math.random() * span)))
    } catch (e) {
      last = e
    }
  }
  throw last
}

export async function startCluster(bin: string, work: string): Promise<Cluster> {
  const root = join(work, `pgtest-${process.pid}-${Date.now()}`)
  mkdirSync(root, { recursive: true })
  execFileSync(join(bin, 'initdb'), ['-D', join(root, 'data'), '-U', 'postgres', '--auth=trust', '-E', 'UTF8'], { stdio: 'ignore' })
  const port = await withFreePort(async (p) => {
    execFileSync(
      join(bin, 'pg_ctl'),
      ['-D', join(root, 'data'), '-w', '-l', join(root, 'log'), '-o', `-p ${p} -k ${root} -c listen_addresses=127.0.0.1 -c max_connections=100 -c track_commit_timestamp=on`, 'start'],
      { stdio: 'ignore' },
    )
    return p
  }, PORT_BASE, PORT_SPAN)
  const probe = new Cluster(bin, root, port, 0)
  const major = await probe.admin(async (c) => Math.floor(Number((await c.query("SHOW server_version_num")).rows[0].server_version_num) / 10000))
  return new Cluster(bin, root, port, major)
}

export interface Bouncer {
  port: number
  stop(): void
}

export interface BouncerOptions {
  reset: boolean
  /** server connections per login (and in total, with `jobs` logins counted by max_db_connections) */
  pool: number
  maxPreparedStatements: number
}

export async function startBouncer(cluster: Cluster, db: string, work: string, o: BouncerOptions): Promise<Bouncer> {
  const root = join(cluster.root, `pgb-${o.reset ? 'reset' : 'noreset'}-${o.maxPreparedStatements}`)
  mkdirSync(root, { recursive: true })
  return withFreePort((port) => launchBouncer(cluster, db, root, port, o), PORT_BASE, PORT_SPAN)
}

async function launchBouncer(cluster: Cluster, db: string, root: string, port: number, o: BouncerOptions): Promise<Bouncer> {
  writeFileSync(join(root, 'users.txt'), '"pipeshub_node_login" ""\n"pipeshub_jobs" ""\n"pipeshub_migrate" ""\n')
  writeFileSync(
    join(root, 'pgb.ini'),
    `[databases]
${db} = host=127.0.0.1 port=${cluster.port} dbname=${db}
[pgbouncer]
listen_addr = 127.0.0.1
listen_port = ${port}
unix_socket_dir =
pool_mode = transaction
auth_type = trust
auth_file = ${root}/users.txt
server_reset_query = ${o.reset ? KERNEL_RESET : ''}
server_reset_query_always = ${o.reset ? 1 : 0}
max_prepared_statements = ${o.maxPreparedStatements}
server_login_retry = 1
max_client_conn = 400
max_db_connections = ${o.pool * 2}
default_pool_size = ${o.pool}
ignore_startup_parameters = extra_float_digits,options
pidfile = ${root}/pgb.pid
logfile = ${root}/pgb.log
`,
  )
  const proc: ChildProcess = spawn(PGBOUNCER, [join(root, 'pgb.ini')], { stdio: 'ignore' })
  for (let i = 0; i < 100; i++) {
    if (proc.exitCode !== null) throw new Error('pgbouncer exited (port in use?)')
    const c = new Client({ host: '127.0.0.1', port, user: 'pipeshub_node_login', database: db })
    try {
      await c.connect()
      await c.end()
      await new Promise((r) => setTimeout(r, 200))
      if (proc.exitCode !== null) throw new Error('pgbouncer exited (port in use?)')
      return { port, stop: () => void proc.kill('SIGTERM') }
    } catch {
      await new Promise((r) => setTimeout(r, 100))
    }
  }
  proc.kill('SIGTERM')
  throw new Error('pgbouncer did not start')
}

/**
 * A fixture schema on top of the real kernel: roles from the bootstrap, the migrated kernel and tenancy tooling
 * (`apply_rls`), one org-scoped table with a jobs policy and one internal counter table. The fence is the kernel's own
 * `kernel.assert_profile_epoch`.
 */
export const fixtureSql = (db: string) => `
SET ROLE pipeshub_owner;
CREATE SCHEMA kt;
CREATE TABLE kt.docs (org_id text NOT NULL, id text NOT NULL, body text, version int NOT NULL DEFAULT 0, PRIMARY KEY (org_id, id));
SELECT kernel.apply_rls('kt.docs', 'org', p_jobs => true);
CREATE TABLE kt.counter (k text PRIMARY KEY, n int NOT NULL DEFAULT 0, note text);
SELECT kernel.apply_rls('kt.counter', 'internal');
INSERT INTO kt.counter (k) VALUES ('a'), ('b');
GRANT SELECT, INSERT, UPDATE, DELETE ON kt.docs, kt.counter TO pipeshub_node, pipeshub_jobs;
GRANT USAGE ON SCHEMA kt TO pipeshub_app, pipeshub_jobs;
RESET ROLE;
GRANT TEMPORARY ON DATABASE ${db} TO pipeshub_node_login; -- the bootstrap revokes it from PUBLIC; PostgreSQL checks the session user
`

const BACKEND = join(__dirname, '../../../../..')
const py = (module: string, env: Record<string, string>, ...args: string[]) =>
  execFileSync(MIGRATE_PY, ['-m', module, ...args], { cwd: BACKEND, stdio: 'pipe', env: { ...process.env, PYTHONPATH: BACKEND, ...env } })

/** Bootstrap the roles, migrate to heads (surface check on) and add the fixture schema. */
export async function createKernelDb(cluster: Cluster, name: string): Promise<void> {
  await cluster.admin((c) => c.query(`CREATE DATABASE ${name}`))
  // the roles and the baseline extension (an extension needs a superuser: the administrator installs it)
  py('db.bootstrap_roles', { PG_ADMIN_URL: `postgresql://postgres@127.0.0.1:${cluster.port}/${name}`, PG_BOOTSTRAP_EXTENSIONS: 'pg_trgm' })
  py('db.migrate', { PG_MIGRATE_URL: `postgresql+psycopg://pipeshub_migrate@127.0.0.1:${cluster.port}/${name}` })
  await cluster.admin((c) => c.query(fixtureSql(name)), name)
}
