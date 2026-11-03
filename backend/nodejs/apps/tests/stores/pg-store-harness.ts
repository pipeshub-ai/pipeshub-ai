/**
 * A real PostgreSQL (migrated to Alembic head, row-level security on) behind PgBouncer, for running the repository
 * contract suites against the PostgreSQL stores. The suites call repositories without an organisation context, as
 * the Mongo ones do; a unit of work on PostgreSQL runs as an organisation, so `scoped` wraps a repository and runs each
 * call in the context of the organisation its arguments name.
 */
import { tmpdir } from 'node:os';
import { Client, Pool } from 'pg';
import { PgRuntime, QueryCatalog, configurePg, createPgPool } from '../../src/libs/db';
import { makeOrgContext, runWithOrgContext } from '../../src/libs/context/orgContext';
import { PgUnitOfWork } from '../../src/libs/repositories/pg';
import { newObjectId } from '../../src/libs/repositories/pg-util';
import type { Tx } from '../../src/libs/repositories/uow';
import { Bouncer, Cluster, createKernelDb, haveStack, PG_BINS, startBouncer, startCluster } from '../libs/db/pg-harness';
import type { ScopedUnitOfWork } from './contract/unit-of-work-scope';

export { PG_BINS, haveStack };

export interface PgStack {
  cluster: Cluster;
  bouncer: Bouncer;
  pool: Pool;
  rt: PgRuntime;
  /** Every statement the repositories sent, by tag (the first sample of each). */
  catalog: QueryCatalog;
  db: string;
  /** A superuser session (outside every policy), for probes and fixtures. */
  admin<T>(fn: (c: Client) => Promise<T>): Promise<T>;
  /** A session through PgBouncer as the application login (no role until a unit switches). */
  stop(): Promise<void>;
}

export async function startPgStack(bin: string, db = 'conversation_stores', opts: { pool?: number; clientPool?: number; jobs?: boolean; importer?: boolean } = {}): Promise<PgStack> {
  const work = process.env.PG_TEST_WORK ?? tmpdir();
  const cluster = await startCluster(bin, work);
  let bouncer: PgStack['bouncer'] | undefined;
  let pool: Pool | undefined;
  let jobsPool: Pool | undefined;
  let rt: PgRuntime;
  let catalog: QueryCatalog;
  try {
    await createKernelDb(cluster, db);
    // the import login is NOLOGIN until an operator enables it for a move; the tests do that
    if (opts.importer) await cluster.admin((c) => c.query('ALTER ROLE pipeshub_import LOGIN'), db);
    bouncer = await startBouncer(cluster, db, work, { reset: true, pool: opts.pool ?? 4, maxPreparedStatements: 200 });
    pool = createPgPool({
      connectionString: cluster.url(db, 'pipeshub_node_login', bouncer.port),
      service: 'node',
      version: 'test',
      max: opts.clientPool ?? 12,
    });
    // the copy of an existing install writes with its own login, direct to PostgreSQL (the PgBouncer here lists no such login)
    jobsPool = opts.importer
      ? createPgPool({ connectionString: cluster.url(db, 'pipeshub_import'), service: 'import', version: 'test', max: 4 })
      : opts.jobs
      ? createPgPool({ connectionString: cluster.url(db, 'pipeshub_jobs', bouncer.port), service: 'jobs', version: 'test', max: 4 })
      : undefined;
    catalog = new QueryCatalog();
    rt = new PgRuntime({ pool, ...(jobsPool && { jobsPool }), defaultService: 'node', catalog, ...(!opts.importer && { fence: { fn: 'kernel.assert_store_epoch', epoch: 1 } }) });
    configurePg(rt);
  } catch (e) {
    // Nothing returned means nobody can call stop(): put back what already started, then report the real failure.
    try {
      await pool?.end().catch(() => undefined);
      await jobsPool?.end().catch(() => undefined);
    } finally {
      try {
        bouncer?.stop();
      } finally {
        cluster.stop();
      }
    }
    throw e;
  }
  return {
    cluster,
    bouncer,
    pool,
    rt,
    catalog,
    db,
    admin: (fn) => cluster.admin(fn, db),
    async stop() {
      configurePg(undefined);
      try {
        await pool.end().catch(() => undefined);
        await jobsPool?.end().catch(() => undefined);
      } finally {
        try {
          bouncer.stop();
        } finally {
          cluster.stop();
        }
      }
    },
  };
}

export const HEX24 = /^[0-9a-f]{24}$/;
export const newId = (): string => newObjectId();

export const inOrg = <T>(org: string, fn: () => T, userId = 'u1'): T =>
  runWithOrgContext(makeOrgContext({ orgId: org, userId, kind: 'user', source: 'jwt', requestId: 'r1' }), fn);

/** What the arguments of a call say about the organisation, learned from the calls before it. */
export class OrgLedger {
  private readonly owner = new Map<string, string>();
  private last: string | undefined;

  private walk(v: unknown, visit: (key: string | undefined, value: unknown, parent: Record<string, unknown> | undefined) => void, depth = 0, key?: string, parent?: Record<string, unknown>): void {
    visit(key, v, parent);
    if (depth > 6 || v === null || typeof v !== 'object' || v instanceof Date) return;
    if (v instanceof Map) {
      for (const [k, x] of v) this.walk(x, visit, depth + 1, String(k), undefined);
      return;
    }
    const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v);
    for (const [k, x] of entries) this.walk(x, visit, depth + 1, k, Array.isArray(v) ? undefined : (v as Record<string, unknown>));
  }

  /** Remember which organisation every id in the value belongs to (an object that names an `orgId` owns the ids it holds). */
  learn(value: unknown): void {
    this.walk(value, (_k, v) => {
      if (v === null || typeof v !== 'object' || v instanceof Date || Array.isArray(v) || v instanceof Map) return;
      const o = v as Record<string, unknown>;
      const org = o.orgId;
      if (typeof org !== 'string' || !HEX24.test(org)) return;
      this.last = org;
      for (const x of Object.values(o)) if (typeof x === 'string' && HEX24.test(x)) this.owner.set(x, org);
    });
  }

  private find(args: unknown[], pick: (v: unknown, k: string | undefined, parent: Record<string, unknown> | undefined) => string | undefined): string | undefined {
    let found: string | undefined;
    for (const a of args) this.walk(a, (k, v, parent) => (found ??= pick(v, k, parent)));
    return found;
  }

  orgFor(args: unknown[], positional?: number): string {
    const org =
      (positional !== undefined && typeof args[positional] === 'string' ? (args[positional] as string) : undefined) ??
      this.find(args, (v, k) => (k === 'orgId' && typeof v === 'string' && HEX24.test(v) ? v : undefined)) ??
      this.find(args, (v) => (typeof v === 'string' ? this.owner.get(v) : undefined)) ??
      this.last;
    if (!org) throw new Error('cannot tell which organisation this call is for');
    this.last = org;
    return org;
  }
}

/** Positional index of the `orgId` argument, per method; methods not listed take it from an object argument or an id they were given before. */
export type OrgArgs = Record<string, number>;

export function scoped<T extends object>(repo: T, ledger: OrgLedger, orgArgs: OrgArgs = {}): T {
  return new Proxy(repo, {
    get(target, prop, receiver) {
      const v = Reflect.get(target, prop, receiver) as unknown;
      if (typeof v !== 'function' || typeof prop !== 'string') return v;
      return (...args: unknown[]) => {
        const org = ledger.orgFor(args, orgArgs[prop]);
        ledger.learn(args);
        const out = inOrg(org, () => (v as (...a: unknown[]) => unknown).apply(target, args)) as Promise<unknown>;
        return out.then((r) => {
          ledger.learn(r);
          return r;
        });
      };
    },
  });
}

/** A unit of work that runs as the organisation it is told (`opts.org`). */
export function scopedUow(): ScopedUnitOfWork {
  const uow = new PgUnitOfWork();
  return {
    run<R>(fn: (tx: Tx) => Promise<R>, opts?: { readOnly?: boolean; org?: string; user?: string }): Promise<R> {
      if (!opts?.org) throw new Error('a PostgreSQL unit of work in a test needs the organisation (opts.org)');
      return inOrg(opts.org, () => uow.run(fn, { readOnly: opts.readOnly }), opts.user);
    },
  };
}
