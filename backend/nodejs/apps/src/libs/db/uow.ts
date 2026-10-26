import { AsyncLocalStorage } from 'node:async_hooks';
import { createHash } from 'node:crypto';
import type { Kysely } from 'kysely';
import {
  MissingOrgContext,
  OrgContext,
  SYSTEM_ORG_ID,
  SystemContextNotAllowed,
  currentOrgContext,
  currentOrgContextOrSystem,
} from '../context/orgContext';
import { QueryCatalog } from './catalog';
import {
  ConnectionLost,
  DbFenced,
  DbReadOnly,
  DbTimeout,
  FailedTransaction,
  PoolTimeout,
  DeadlockDetected,
  LockNotAvailable,
  PrologueFailed,
  SerializationFailure,
  SessionRoleLeak,
  UowUsageError,
  WriteIsolationRefused,
  isRetryable,
  mapPgError,
} from './errors';
import { kyselyOn } from './kysely';
import { TAG_RE, lintSql } from './lint';
import { LockKey, lockKeyArgs } from './lock-keys';
import { metrics } from './metrics';

export type Isolation = 'read_committed' | 'repeatable_read' | 'serializable';
export type Pooler =
  'pgbouncer_txn' | 'none' | 'azure_pgbouncer' | 'pgbouncer_session';

/** The slice of `pg.PoolClient` the runtime uses. */
export interface PgClient {
  query(q: {
    text: string;
    values?: unknown[];
    name?: string;
  }): Promise<{ rows: unknown[]; rowCount: number | null; command?: string }>;
  release(destroy?: boolean | Error): void;
  /** pg's ReadyForQuery status: 'I' idle (no transaction), 'T' in a transaction, 'E' failed transaction. */
  getTransactionStatus?(): string | null;
  on?(event: 'error', listener: (e: Error) => void): unknown;
  removeListener?(event: 'error', listener: (e: Error) => void): unknown;
}
/** The slice of `pg.Pool` the runtime uses. */
export interface PgPool {
  connect(): Promise<PgClient>;
}

export interface UowOptions {
  /** Role suffix: `node` runs as `pipeshub_node`. Defaults to the runtime's service. */
  service?: string;
  /** Read-only unless set. A write in a read-only unit fails with DbReadOnly. */
  write?: boolean;
  timeoutMs?: number;
  lockTimeoutMs?: number;
  isolation?: Isolation;
  /** Also set `app.org_lineage` (computed by the server). */
  lineage?: boolean;
  /** Force custom plans for this transaction; the only mode in which named statements are accepted. */
  customPlan?: boolean;
  /** Retry on connection loss even after statements ran (never after COMMIT was sent). */
  idempotent?: boolean;
  /** Run with no org (`app.org_id = ''`): lookups that happen before the org is known. Explicit, never inferred. */
  preOrg?: boolean;
  workMem?: string;
}

export interface PgRuntimeConfig {
  pool: PgPool;
  /** Only processes that run jobs have one. */
  jobsPool?: PgPool;
  defaultService: string;
  pooler?: Pooler;
  /** Write units call `fn(epoch)` in the prologue. `fn` is a schema-qualified function name. */
  fence?: { fn: string; epoch: number | (() => number) };
  /** Every unit is read-only, whatever the caller asked for: the shadow-read runtime (DOC_STORE=shadow) must never write. */
  readOnly?: boolean;
  catalog?: QueryCatalog;
  /** May only lower the class defaults. */
  orgLimits?: (
    ctx: OrgContext,
  ) => { timeoutMs?: number; lockTimeoutMs?: number } | undefined;
  sleep?: (ms: number) => Promise<void>;
}

export interface StatementConfig {
  text: string;
  values?: unknown[];
  /** Named statements are planned once and may turn generic; accepted only in a `customPlan` unit. */
  name?: string;
}

export type UowResult<R = Record<string, unknown>> = {
  rows: R[];
  rowCount: number | null;
};

// timeouts by class (ms): [statement, lock]
const CLASS_TIMEOUTS: Record<string, [number, number]> = {
  node: [15_000, 2_000],
  query: [5_000, 2_000],
  connectors: [60_000, 5_000],
  indexing: [60_000, 5_000],
  jobs: [900_000, 5_000],
  // the one-off copy of an existing install: its statements read and write whole tables (a verify counts a million rows), like a job's
  import: [900_000, 5_000],
};
const MAX_RETRIES = 3;
const RETRY_BASE_MS = 10;
const SERVICE_RE = /^[a-z][a-z0-9_]{0,30}$/;
const FENCE_FN_RE = /^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$/;
const REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,64}$/;

/** Client headers reach `app.request_id`, so anything outside the safe shape is replaced by a hash. */
export function sanitizeRequestId(raw: string | null | undefined): string {
  if (raw && REQUEST_ID_RE.test(raw)) return raw;
  return `invalid-${createHash('sha256')
    .update(raw ?? '')
    .digest('hex')
    .slice(0, 12)}`;
}

const BEGIN: Record<Isolation, string> = {
  read_committed: 'BEGIN ISOLATION LEVEL READ COMMITTED',
  repeatable_read: 'BEGIN ISOLATION LEVEL REPEATABLE READ',
  serializable: 'BEGIN ISOLATION LEVEL SERIALIZABLE',
};

/**
 * One statement, one round trip. `pre` reads the role before the switch (the guard); the LATERAL is correlated
 * to it so it runs after. The fence column is selected so the planner cannot drop the call.
 * The login needs USAGE/EXECUTE on the fence function, because the statement is checked before the role switch.
 */
function prologueSql(fence: string | undefined): string {
  return `WITH pre AS MATERIALIZED (SELECT current_setting('role') AS r)
SELECT pre.r, s.fence, s.ts FROM pre, LATERAL (
  SELECT set_config('role', $1, true), set_config('app.org_id', $2, true), set_config('app.request_id', $3, true),
         set_config('app.user_id', $4, true), set_config('statement_timeout', $5, true),
         set_config('lock_timeout', $6, true), set_config('plan_cache_mode', $7, true),
         set_config('work_mem', $8, true),
         now()::text AS ts,
         ${fence ? `CASE WHEN $9::boolean THEN ${fence}($10::int) END` : 'NULL::int'} AS fence
  WHERE pre.r IS NOT NULL) s`;
}

interface Binding {
  role: string;
  orgId: string;
  userId: string;
  requestId: string;
  ctx?: OrgContext;
}

class Attempt {
  statements = 0;
  commitSent = false;
  /** The transaction timestamp of a write unit; a different one before COMMIT means the transaction ended early. */
  startedAt?: string;
  /** The connection no longer holds the unit's transaction; it is discarded, never returned to the pool. */
  broken = false;
}

export class Tx {
  private active = true;
  constructor(
    private readonly client: PgClient,
    private readonly run: Attempt,
    private readonly rt: PgRuntime,
    readonly opts: UowOptions,
    readonly service: string,
    readonly orgId: string,
    readonly requestId: string,
  ) {}

  end(): void {
    this.active = false;
  }

  get isActive(): boolean {
    return this.active;
  }

  /**
   * Run `fn` so that a failure inside it can be caught without aborting the transaction (a duplicate key that is
   * handled, a timeout on one optional statement). Rolls back to the savepoint and rethrows when `fn` throws.
   */
  async savepoint<T>(name: string, fn: () => Promise<T>): Promise<T> {
    if (!/^[a-z_][a-z0-9_]{0,40}$/.test(name))
      throw new UowUsageError(`bad savepoint name '${name}'`);
    await this.exec({ text: `SAVEPOINT ${name}` });
    try {
      const out = await fn();
      await this.exec({ text: `RELEASE SAVEPOINT ${name}` });
      return out;
    } catch (e) {
      try {
        await this.exec({ text: `ROLLBACK TO SAVEPOINT ${name}` });
        await this.exec({ text: `RELEASE SAVEPOINT ${name}` });
      } catch {
        // the connection is gone or the transaction cannot continue; the original error is the useful one
      }
      throw e;
    }
  }

  query<R = Record<string, unknown>>(
    textOrConfig: string | StatementConfig,
    values?: unknown[],
  ): Promise<UowResult<R>> {
    const cfg =
      typeof textOrConfig === 'string'
        ? { text: textOrConfig, values }
        : textOrConfig;
    return this.rt.runStatement(this, cfg) as Promise<UowResult<R>>;
  }

  /** @internal */
  async exec(cfg: StatementConfig): Promise<UowResult> {
    if (!this.active)
      throw new UowUsageError(
        'the unit of work has finished; do not keep its handle',
      );
    this.run.statements++;
    try {
      const res = (await this.client.query(cfg)) as UowResult;
      // a statement that smuggled a COMMIT/ROLLBACK past the text guard leaves the connection idle
      if (this.client.getTransactionStatus?.() === 'I') {
        this.run.broken = true;
        throw new FailedTransaction();
      }
      return res;
    } catch (e) {
      throw mapPgError(e);
    }
  }

  /** Blocks until the lock is free, then holds it to commit. */
  async advisoryXactLock(
    key: LockKey,
    objectId: number,
    shared = false,
  ): Promise<void> {
    const fn = shared
      ? 'pg_advisory_xact_lock_shared'
      : 'pg_advisory_xact_lock';
    await this.query(
      `/* pipeshub:kernel.advisory_lock */ SELECT ${fn}($1::int, $2::int)`,
      lockKeyArgs(key, objectId),
    );
  }

  /** Returns false when someone else holds it. */
  async tryAdvisoryXactLock(
    key: LockKey,
    objectId: number,
    shared = false,
  ): Promise<boolean> {
    const fn = shared
      ? 'pg_try_advisory_xact_lock_shared'
      : 'pg_try_advisory_xact_lock';
    const r = await this.query<{ ok: boolean }>(
      `/* pipeshub:kernel.try_advisory_lock */ SELECT ${fn}($1::int, $2::int) AS ok`,
      lockKeyArgs(key, objectId),
    );
    return r.rows[0]?.ok === true;
  }

  /** A Kysely builder bound to this transaction. Every statement it sends carries `tag` and passes the same guards. */
  db<DB = Record<string, never>>(tag: string): Kysely<DB> {
    if (!TAG_RE.test(`/* pipeshub:${tag} */`))
      throw new UowUsageError(`bad statement tag '${tag}'`);
    return kyselyOn<DB>((text, values) =>
      this.query({
        text: `/* pipeshub:${tag} */ ${text}`,
        values: [...values],
      }),
    );
  }
}

export class PgRuntime {
  private readonly pooler: Pooler;
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(private readonly cfg: PgRuntimeConfig) {
    if (!SERVICE_RE.test(cfg.defaultService))
      throw new UowUsageError(`bad service '${cfg.defaultService}'`);
    if (cfg.fence && !FENCE_FN_RE.test(cfg.fence.fn))
      throw new UowUsageError(`bad fence function '${cfg.fence.fn}'`);
    this.pooler = cfg.pooler ?? 'pgbouncer_txn';
    this.sleep = cfg.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  }

  /** Org unit: the org comes from the ambient OrgContext. A missing context fails before any connection is taken. */
  async withUow<T>(opts: UowOptions, fn: (tx: Tx) => Promise<T>): Promise<T> {
    const b = this.bindOrg(opts);
    refuseNesting();
    return this.retrying(this.cfg.pool, b, opts, fn);
  }

  /** Job processes only: the jobs pool and role. A system context runs with no org. */
  async withJobsUow<T>(
    opts: UowOptions,
    fn: (tx: Tx) => Promise<T>,
  ): Promise<T> {
    if (!this.cfg.jobsPool)
      throw new UowUsageError('this process has no jobs pool');
    let ctx: OrgContext;
    try {
      ctx = currentOrgContextOrSystem();
    } catch (e) {
      this.countMissing(e);
    }
    refuseNesting();
    // `service: 'import'` is the one-off copy of an existing install: a login of its own, on this pool
    const service = opts.service === 'import' ? 'import' : 'jobs';
    const b = this.binding(service, ctx.orgId ?? '', ctx);
    return this.retrying(this.cfg.jobsPool, b, { ...opts, service }, fn);
  }

  private countMissing(e: unknown): never {
    if (e instanceof MissingOrgContext) metrics.orgContextMissing.inc();
    throw e;
  }

  private binding(service: string, orgId: string, ctx?: OrgContext): Binding {
    if (!SERVICE_RE.test(service))
      throw new UowUsageError(`bad service '${service}'`);
    return {
      role: `pipeshub_${service}`,
      orgId,
      userId: ctx?.userId ?? '',
      requestId: sanitizeRequestId(ctx?.requestId),
      ctx,
    };
  }

  private bindOrg(opts: UowOptions): Binding {
    const service = opts.service ?? this.cfg.defaultService;
    if (opts.preOrg) return this.binding(service, '');
    let ctx: OrgContext;
    try {
      ctx = currentOrgContext();
    } catch (e) {
      this.countMissing(e);
    }
    if (ctx.orgId === SYSTEM_ORG_ID)
      throw new SystemContextNotAllowed(ctx.source);
    return this.binding(service, ctx.orgId as string, ctx);
  }

  private async retrying<T>(
    pool: PgPool,
    b: Binding,
    opts: UowOptions,
    fn: (tx: Tx) => Promise<T>,
  ): Promise<T> {
    if (this.cfg.readOnly === true && opts.write === true)
      opts = { ...opts, write: false };
    const service = b.role.slice('pipeshub_'.length);
    const write = opts.write ?? false;
    const isolation = opts.isolation ?? 'read_committed';
    if (write && isolation !== 'read_committed')
      throw new WriteIsolationRefused(isolation);
    const started = process.hrtime.bigint();
    let retries = 0;
    let connRetries = 0;
    for (;;) {
      const run = new Attempt();
      try {
        const out = await this.attempt(pool, b, opts, run, fn);
        metrics.uow.observe(
          { service, outcome: 'commit' },
          secondsSince(started),
        );
        return out;
      } catch (e) {
        const lostEarly =
          (e instanceof ConnectionLost &&
            !e.commitSent &&
            (run.statements === 0 || opts.idempotent === true)) ||
          (e instanceof PoolTimeout && (!write || opts.idempotent === true));
        if (isRetryable(e) && retries < MAX_RETRIES) {
          const code = (e as SerializationFailure | DeadlockDetected)
            .sqlstate as string;
          metrics.retries.inc({ code });
          await this.sleep(backoff(retries++));
        } else if (lostEarly && connRetries < 1) {
          connRetries++;
          metrics.retries.inc({ code: 'connection' });
          await this.sleep(backoff(0));
        } else {
          metrics.uow.observe(
            { service, outcome: outcomeOf(e) },
            secondsSince(started),
          );
          throw e;
        }
      }
    }
  }

  private async attempt<T>(
    pool: PgPool,
    b: Binding,
    opts: UowOptions,
    run: Attempt,
    fn: (tx: Tx) => Promise<T>,
  ): Promise<T> {
    const waited = process.hrtime.bigint();
    let client: PgClient;
    try {
      client = await pool.connect();
    } catch (e) {
      throw mapPgError(e);
    }
    metrics.poolWait.observe({}, secondsSince(waited));
    let destroy = false;
    let tx: Tx | undefined;
    // The pool detaches its own handler while a client is checked out; a connection killed between two statements
    // would otherwise be an uncaught 'error' event. The next statement reports the loss.
    const onClientError = (): void => {
      destroy = true;
    };
    client.on?.('error', onClientError);
    try {
      try {
        await client.query({
          text:
            BEGIN[opts.isolation ?? 'read_committed'] +
            (opts.write ? '' : ' READ ONLY'),
        });
        const pro = await client.query({
          text: this.prologue(),
          values: this.prologueValues(b, opts),
        });
        const row = pro.rows[0] as { r?: string; ts?: string } | undefined;
        const leaked = row?.r;
        run.startedAt = row?.ts;
        if (leaked !== 'none') {
          destroy = true;
          metrics.roleLeak.inc();
          throw new SessionRoleLeak(String(leaked));
        }
        if (opts.lineage) {
          await client.query({
            text: "/* pipeshub:kernel.org_lineage */ SELECT set_config('app.org_lineage', array_to_string(kernel.org_lineage(current_setting('app.org_id')), ','), true)",
          });
        }
      } catch (e) {
        throw this.prologueError(e);
      }
      tx = new Tx(
        client,
        run,
        this,
        opts,
        b.role.slice('pipeshub_'.length),
        b.orgId,
        b.requestId,
      );
      const unit = tx;
      const out = await unitStorage.run(unit, () => fn(unit));
      if (opts.write) await this.confirmStillOpen(client, run);
      run.commitSent = true;
      const done = await client.query({ text: 'COMMIT' });
      // COMMIT of an aborted transaction answers ROLLBACK without an error: the writes are gone
      if (done.command === 'ROLLBACK') throw new FailedTransaction();
      return out;
    } catch (e) {
      const mapped = mapPgError(e, run.commitSent);
      if (mapped instanceof ConnectionLost || run.broken) destroy = true;
      if (!run.commitSent && !(mapped instanceof ConnectionLost)) {
        try {
          await client.query({ text: 'ROLLBACK' });
        } catch {
          destroy = true;
        }
      }
      throw mapped;
    } finally {
      tx?.end();
      client.removeListener?.('error', onClientError);
      client.release(destroy || undefined);
    }
  }

  /**
   * One round trip before COMMIT: the transaction timestamp must still be the prologue's. A statement that ended the
   * transaction and began another (`COMMIT; BEGIN`) changes it, and a plain COMMIT leaves nothing to answer in.
   */
  private async confirmStillOpen(
    client: PgClient,
    run: Attempt,
  ): Promise<void> {
    try {
      const r = await client.query({
        text: '/* pipeshub:kernel.commit_probe */ SELECT now()::text AS ts',
      });
      if ((r.rows[0] as { ts?: string } | undefined)?.ts === run.startedAt)
        return;
    } catch (e) {
      const m = mapPgError(e);
      if (!(m instanceof FailedTransaction)) throw m;
    }
    run.broken = true;
    throw new FailedTransaction();
  }

  private prologueError(e: unknown): unknown {
    // 55000 is what the profile epoch function raises on a mismatch
    if ((e as { code?: string }).code === '55000') return new DbFenced(e);
    const m = mapPgError(e);
    const passThrough = [
      ConnectionLost,
      DbFenced,
      DbReadOnly,
      DbTimeout,
      FailedTransaction,
      PoolTimeout,
      LockNotAvailable,
      SerializationFailure,
      DeadlockDetected,
      SessionRoleLeak,
    ];
    if (passThrough.some((c) => m instanceof c)) return m;
    return new PrologueFailed(e, (e as { code?: string }).code);
  }

  private prologue(): string {
    return prologueSql(this.cfg.fence?.fn);
  }

  private prologueValues(b: Binding, opts: UowOptions): unknown[] {
    const service = b.role.slice('pipeshub_'.length);
    const [stmt, lock] = CLASS_TIMEOUTS[service] ?? [15_000, 2_000];
    const lim = b.ctx ? this.cfg.orgLimits?.(b.ctx) : undefined;
    const lower = (base: number, ...want: Array<number | undefined>): number =>
      Math.min(base, ...want.filter((w): w is number => w !== undefined));
    const values: unknown[] = [
      b.role,
      b.orgId,
      b.requestId,
      b.userId,
      String(lower(opts.timeoutMs ?? stmt, lim?.timeoutMs)),
      String(lower(opts.lockTimeoutMs ?? lock, lim?.lockTimeoutMs)),
      opts.customPlan ? 'force_custom_plan' : 'auto',
      opts.workMem ?? null,
    ];
    if (this.cfg.fence) {
      const e = this.cfg.fence.epoch;
      values.push(opts.write === true, typeof e === 'function' ? e() : e);
    }
    return values;
  }

  /** @internal */
  async runStatement(tx: Tx, st: StatementConfig): Promise<UowResult> {
    const problems = lintSql(st.text);
    if (problems.length)
      throw new UowUsageError(`statement refused: ${problems.join('; ')}`);
    if (st.name && !this.namedAllowed(tx)) {
      throw new UowUsageError(
        'named statements are accepted only in a customPlan unit behind a pooler that tracks them',
      );
    }
    this.cfg.catalog?.record(st.text, st.values ?? []);
    const t0 = process.hrtime.bigint();
    try {
      return await tx.exec(st);
    } finally {
      const site = /pipeshub:(\S+)/.exec(st.text)?.[1] ?? 'untagged';
      metrics.query.observe(
        { service: tx.service, site, class: tx.opts.write ? 'write' : 'read' },
        secondsSince(t0),
      );
    }
  }

  private namedAllowed(tx: Tx): boolean {
    return tx.opts.customPlan === true && this.pooler !== 'azure_pgbouncer';
  }
}

const unitStorage = new AsyncLocalStorage<Tx>();

/** The unit of work running in this async context, if any: code that is handed no `tx` joins it instead of nesting. */
export function activeUnit(): Tx | undefined {
  const t = unitStorage.getStore();
  return t?.isActive ? t : undefined;
}

/** A unit inside a unit takes a second connection and can exhaust the pool; pass `tx` down instead. */
function refuseNesting(): void {
  if (unitStorage.getStore()?.isActive)
    throw new UowUsageError(
      'a unit of work cannot start inside another; pass tx down',
    );
}

function backoff(n: number): number {
  return RETRY_BASE_MS * 2 ** n * (0.5 + Math.random());
}

function secondsSince(t0: bigint): number {
  return Number(process.hrtime.bigint() - t0) / 1e9;
}

function outcomeOf(e: unknown): string {
  return e instanceof Error ? e.constructor.name : 'error';
}

let current: PgRuntime | undefined;

/** Select PostgreSQL for this process. Nothing calls this unless a feature opts in. */
export function configurePg(rt: PgRuntime | undefined): void {
  current = rt;
}

export function pgRuntime(): PgRuntime {
  if (!current)
    throw new UowUsageError('PostgreSQL is not configured for this process');
  return current;
}

export async function withUow<T>(
  opts: UowOptions,
  fn: (tx: Tx) => Promise<T>,
): Promise<T> {
  return pgRuntime().withUow(opts, fn);
}

export async function withJobsUow<T>(
  opts: UowOptions,
  fn: (tx: Tx) => Promise<T>,
): Promise<T> {
  return pgRuntime().withJobsUow(opts, fn);
}
