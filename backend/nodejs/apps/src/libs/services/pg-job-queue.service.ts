import { EventEmitter } from 'node:events';
import { createHash, randomUUID } from 'node:crypto';
import { getNextMillis } from 'bullmq';
import { Logger } from './logger.service';
import { ensurePgRuntime } from '../db/runtime-boot';
import { PgListener, listenUrl } from '../db/listen';
import type { Tx } from '../db/uow';

/**
 * A job queue and scheduler on PostgreSQL, with the slice of the BullMQ `Queue` / `Worker` / `Job` surface the crawl scheduler uses,
 * so `CrawlingSchedulerService` runs on it unchanged. For a deployment with no Redis; with Redis configured BullMQ stays the default.
 *
 * - **A run is a row** (`platform.sched_jobs`): waiting or delayed until `run_at`, then active, then completed or failed.
 * - **A schedule is a row** (`platform.sched_repeats`): `add(..., {repeat})` stores it and its first run; *claiming* a run inserts the
 *   next one, under an id made from the schedule's key and the run time, so the same run can never be inserted twice.
 * - **Claim** is `FOR UPDATE SKIP LOCKED` in message order of (priority, run time): two Node processes never start the same run.
 * - **Wake-up** is NOTIFY on `pipeshub_sched`, plus one timer to the earliest run time or claim expiry (no polling).
 * - A claimed run is kept claimed by a heartbeat; one whose worker died is put back (`stalled`) or failed after `maxStalledCount`.
 * - A job id that is already taken is not added again, whatever its state (what BullMQ does).
 */
export const SCHED_CHANNEL = 'pipeshub_sched';

export type PgJobState =
  | 'waiting'
  | 'delayed'
  | 'active'
  | 'completed'
  | 'failed'
  | 'prioritized'
  | 'paused'
  | 'unknown';

export interface PgJobOptions {
  priority?: number;
  attempts?: number;
  delay?: number;
  jobId?: string;
  removeOnComplete?: number | boolean;
  removeOnFail?: number | boolean;
  backoff?: { type: 'exponential' | 'fixed'; delay: number };
  repeat?: {
    pattern?: string;
    every?: number;
    tz?: string;
    endDate?: number | string | Date;
    limit?: number;
    key?: string;
  };
}

interface JobRow {
  id: string;
  name: string;
  data: Record<string, unknown>;
  opts: PgJobOptions;
  state: PgJobState;
  created_ms: string;
  delay_ms: string | null;
  processed_ms: string | null;
  finished_ms: string | null;
  attempts_made: number;
  progress: unknown;
  failed_reason: string | null;
  /** claim only: when the run was due, and the database's clock at the claim */
  run_ms?: string;
  db_now_ms?: string;
}

interface RepeatRow {
  key: string;
  name: string;
  job_id: string | null;
  pattern: string | null;
  every_ms: string | null;
  tz: string | null;
  end_date_ms: string | null;
  next_ms: string | null;
}

const SQL = {
  insertJob: `/* pipeshub:sched.insertjob */ INSERT INTO platform.sched_jobs (queue, id, name, data, opts, state, run_at, priority, created_ms, delay_ms)
    VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, now() + make_interval(secs => $7::double precision), $8, $9, $10)
    ON CONFLICT (queue, id) DO NOTHING RETURNING id, name, data, opts, state, created_ms::text AS created_ms, delay_ms::text AS delay_ms, processed_ms::text AS processed_ms, finished_ms::text AS finished_ms, attempts_made, progress, failed_reason`,
  dbNow: `/* pipeshub:sched.dbnow */ SELECT (extract(epoch FROM now()) * 1000)::bigint::text AS ms`,
  getJob: `/* pipeshub:sched.getjob */ SELECT id, name, data, opts, state, created_ms::text AS created_ms, delay_ms::text AS delay_ms, processed_ms::text AS processed_ms, finished_ms::text AS finished_ms, attempts_made, progress, failed_reason FROM platform.sched_jobs WHERE queue = $1 AND id = $2`,
  getJobs: `/* pipeshub:sched.getjobs */ SELECT id, name, data, opts, state, created_ms::text AS created_ms, delay_ms::text AS delay_ms, processed_ms::text AS processed_ms, finished_ms::text AS finished_ms, attempts_made, progress, failed_reason FROM platform.sched_jobs WHERE queue = $1 AND state = ANY($2::text[])
    ORDER BY created_ms DESC, id`,
  stateOf: `/* pipeshub:sched.stateof */ SELECT state FROM platform.sched_jobs WHERE queue = $1 AND id = $2`,
  removeJob: `/* pipeshub:sched.removejob */ DELETE FROM platform.sched_jobs WHERE queue = $1 AND id = $2 AND state <> 'active' RETURNING id`,
  progress: `/* pipeshub:sched.progress */ UPDATE platform.sched_jobs SET progress = $3::jsonb WHERE queue = $1 AND id = $2`,
  upsertRepeat: `/* pipeshub:sched.upsertrepeat */ INSERT INTO platform.sched_repeats (queue, key, name, job_id, pattern, every_ms, tz, end_date_ms, next_ms)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    ON CONFLICT (queue, key) DO UPDATE SET next_ms = excluded.next_ms`,
  repeats: `/* pipeshub:sched.repeats */ SELECT key, name, job_id, pattern, every_ms::text AS every_ms, tz, end_date_ms::text AS end_date_ms, next_ms::text AS next_ms
    FROM platform.sched_repeats WHERE queue = $1 ORDER BY key`,
  removeRepeat: `/* pipeshub:sched.removerepeat */ DELETE FROM platform.sched_repeats WHERE queue = $1 AND key = $2 RETURNING key`,
  // SKIP LOCKED: a run a worker is claiming right now is left to it (it becomes the last run); removing never waits on a claimer
  removeRepeatRuns: `/* pipeshub:sched.removerepeatruns */ DELETE FROM platform.sched_jobs j USING (
      SELECT queue, id FROM platform.sched_jobs
      WHERE queue = $1 AND state IN ('waiting', 'delayed') AND opts #>> '{repeat,key}' = $2 FOR UPDATE SKIP LOCKED) p
    WHERE j.queue = p.queue AND j.id = p.id`,
  notify: `/* pipeshub:sched.notify */ SELECT pg_notify('pipeshub_sched', $1)`,
  claim: `/* pipeshub:sched.claim */
    WITH picked AS (
      SELECT queue, id FROM platform.sched_jobs
      WHERE queue = $1 AND state IN ('waiting', 'delayed') AND run_at <= now()
      ORDER BY priority, run_at, created_ms LIMIT $2 FOR UPDATE SKIP LOCKED)
    UPDATE platform.sched_jobs j SET state = 'active', attempts_made = j.attempts_made + 1, locked_by = $3,
      locked_until = now() + make_interval(secs => $4::double precision), processed_ms = (extract(epoch FROM now()) * 1000)::bigint
    FROM picked p WHERE j.queue = p.queue AND j.id = p.id RETURNING j.id, j.name, j.data, j.opts, j.state, j.created_ms::text AS created_ms, j.delay_ms::text AS delay_ms, j.processed_ms::text AS processed_ms, j.finished_ms::text AS finished_ms, j.attempts_made, j.progress, j.failed_reason, (extract(epoch FROM j.run_at) * 1000)::bigint::text AS run_ms, (extract(epoch FROM now()) * 1000)::bigint::text AS db_now_ms`,
  nextRun: `/* pipeshub:sched.nextrun */ INSERT INTO platform.sched_jobs (queue, id, name, data, opts, state, run_at, priority, created_ms, delay_ms)
    SELECT $1, $2, $3, $4::jsonb, $5::jsonb, 'delayed', to_timestamp($6::double precision / 1000), $7, $8, $9
    WHERE EXISTS (SELECT 1 FROM platform.sched_repeats WHERE queue = $1 AND key = $10 FOR SHARE)
    ON CONFLICT (queue, id) DO NOTHING`,
  bumpRepeat: `/* pipeshub:sched.bumprepeat */ UPDATE platform.sched_repeats SET next_ms = $3 WHERE queue = $1 AND key = $2`,
  complete: `/* pipeshub:sched.complete */ UPDATE platform.sched_jobs SET state = 'completed', finished_ms = $4, locked_by = NULL, locked_until = NULL, progress = $5::jsonb
    WHERE queue = $1 AND id = $2 AND attempts_made = $3 AND state = 'active' RETURNING id`,
  retry: `/* pipeshub:sched.retry */ UPDATE platform.sched_jobs SET state = 'delayed', failed_reason = $4, locked_by = NULL, locked_until = NULL,
    run_at = now() + make_interval(secs => $5::double precision)
    WHERE queue = $1 AND id = $2 AND attempts_made = $3 AND state = 'active' RETURNING id`,
  fail: `/* pipeshub:sched.fail */ UPDATE platform.sched_jobs SET state = 'failed', failed_reason = $4, finished_ms = $5, locked_by = NULL, locked_until = NULL
    WHERE queue = $1 AND id = $2 AND attempts_made = $3 AND state = 'active' RETURNING id`,
  extend: `/* pipeshub:sched.extend */ UPDATE platform.sched_jobs SET locked_until = now() + make_interval(secs => $3::double precision)
    WHERE queue = $1 AND id = ANY($2::text[]) AND state = 'active'`,
  dropOne: `/* pipeshub:sched.dropone */ DELETE FROM platform.sched_jobs WHERE queue = $1 AND id = $2 AND state = $3`,
  trim: `/* pipeshub:sched.trim */ DELETE FROM platform.sched_jobs WHERE queue = $1 AND state = $2 AND id NOT IN (
      SELECT id FROM platform.sched_jobs WHERE queue = $1 AND state = $2 ORDER BY finished_ms DESC NULLS LAST, id LIMIT $3)`,
  stalledBack: `/* pipeshub:sched.stalledback */ UPDATE platform.sched_jobs SET state = 'waiting', stalled = stalled + 1, locked_by = NULL, locked_until = NULL
    WHERE queue = $1 AND state = 'active' AND locked_until < now() AND stalled < $2 RETURNING id`,
  stalledFail: `/* pipeshub:sched.stalledfail */ UPDATE platform.sched_jobs SET state = 'failed', failed_reason = 'job stalled more than allowable limit',
    finished_ms = $3, locked_by = NULL, locked_until = NULL
    WHERE queue = $1 AND state = 'active' AND locked_until < now() AND stalled >= $2 RETURNING id`,
  nextDue: `/* pipeshub:sched.nextdue */ SELECT extract(epoch FROM min(CASE WHEN state = 'active' THEN locked_until ELSE run_at END) - now())::float8 AS secs
    FROM platform.sched_jobs WHERE queue = $1 AND state IN ('waiting', 'delayed', 'active')`,
};

const unit = <R>(write: boolean, fn: (tx: Tx) => Promise<R>): Promise<R> =>
  ensurePgRuntime().withUow({ preOrg: true, write }, fn);

/** A run, shaped like a BullMQ `Job` for the fields and methods the scheduler reads. */
export class PgJob<D = Record<string, unknown>> {
  readonly id: string;
  readonly name: string;
  readonly data: D;
  readonly opts: PgJobOptions;
  readonly timestamp: number;
  readonly delay: number | undefined;
  progress: unknown;
  readonly attemptsMade: number;
  readonly processedOn: number | undefined;
  readonly finishedOn: number | undefined;
  readonly failedReason: string | undefined;
  /** Set while a worker runs the job, so `updateProgress` reaches the worker's `progress` event. */
  onProgress: ((job: PgJob<D>, progress: unknown) => void) | undefined;

  constructor(
    private readonly queue: string,
    row: JobRow,
  ) {
    this.id = row.id;
    this.name = row.name;
    this.data = row.data as D;
    this.opts = row.opts;
    this.timestamp = Number(row.created_ms);
    this.delay = row.delay_ms === null ? undefined : Number(row.delay_ms);
    this.progress = row.progress ?? 0;
    this.attemptsMade = row.attempts_made;
    this.processedOn =
      row.processed_ms === null ? undefined : Number(row.processed_ms);
    this.finishedOn =
      row.finished_ms === null ? undefined : Number(row.finished_ms);
    this.failedReason = row.failed_reason ?? undefined;
  }

  async getState(): Promise<PgJobState> {
    const r = await unit(false, (tx) =>
      tx.query<{ state: PgJobState }>(SQL.stateOf, [this.queue, this.id]),
    );
    return r.rows[0]?.state ?? 'unknown';
  }

  /** Removes the run. A run a worker holds cannot be removed (BullMQ refuses a locked job the same way). */
  async remove(): Promise<void> {
    const r = await unit(true, (tx) =>
      tx.query(SQL.removeJob, [this.queue, this.id]),
    );
    if (r.rows.length === 0 && (await this.getState()) !== 'unknown')
      throw new Error(
        `Job ${this.id} could not be removed: it is being processed`,
      );
  }

  async updateProgress(progress: unknown): Promise<void> {
    this.progress = progress;
    await unit(true, (tx) =>
      tx.query(SQL.progress, [this.queue, this.id, JSON.stringify(progress)]),
    );
    this.onProgress?.(this, progress);
  }
}

export interface PgRepeatableJob {
  key: string;
  name: string;
  id: string | null;
  endDate: number | null;
  tz: string | null;
  pattern: string | null;
  every: string | null;
  next: number;
}

function repeatKey(
  name: string,
  jobId: string | undefined,
  r: NonNullable<PgJobOptions['repeat']>,
): string {
  const end =
    r.endDate === undefined ? '' : String(new Date(r.endDate).getTime());
  return `${name}:${jobId ?? ''}:${end}:${r.tz ?? ''}:${r.pattern ?? r.every ?? ''}`;
}

const runId = (key: string, at: number): string =>
  `repeat:${createHash('md5').update(key).digest('hex')}:${at}`;

function nextOf(
  r: NonNullable<PgJobOptions['repeat']>,
  from: number,
): number | undefined {
  const next = getNextMillis(from, r);
  if (next === undefined) return undefined;
  const end =
    r.endDate === undefined ? undefined : new Date(r.endDate).getTime();
  return end !== undefined && next > end ? undefined : next;
}

const TYPE_STATES: Record<string, string[]> = {
  waiting: ['waiting'],
  prioritized: [], // a waiting run with a priority is reported as waiting
  delayed: ['delayed'],
  active: ['active'],
  completed: ['completed'],
  failed: ['failed'],
  paused: [],
};

export class PgJobQueue<D = Record<string, unknown>> {
  constructor(
    readonly name: string,
    private readonly defaults: PgJobOptions = {},
  ) {}

  private wrap(row: JobRow): PgJob<D> {
    return new PgJob<D>(this.name, row);
  }

  /** Adds a run, or a schedule and its first run. A job id that is already taken adds nothing and returns the job that holds it. */
  async add(
    name: string,
    data: D,
    options: PgJobOptions = {},
  ): Promise<PgJob<D>> {
    const opts: PgJobOptions = { ...this.defaults, ...options };
    const row = await unit(true, async (tx) => {
      // every time here is the database's: a Node clock that is early or late must not shift a schedule, or end one
      const now = Number(
        (await tx.query<{ ms: string }>(SQL.dbNow)).rows[0]?.ms ?? Date.now(),
      );
      let id = opts.jobId ?? randomUUID();
      let delay = opts.delay ?? 0;
      if (opts.repeat) {
        const r = opts.repeat;
        const key = repeatKey(name, opts.jobId, r);
        const next = nextOf(r, now);
        if (next === undefined)
          throw new Error('Repeat options never produce a run time');
        id = runId(key, next);
        delay = Math.max(next - now, 0);
        opts.repeat = { ...r, key };
        await tx.query(SQL.upsertRepeat, [
          this.name,
          key,
          name,
          opts.jobId ?? null,
          r.pattern ?? null,
          r.every ?? null,
          r.tz ?? null,
          r.endDate === undefined ? null : new Date(r.endDate).getTime(),
          next,
        ]);
        delete opts.jobId;
      }
      const ins = await tx.query<JobRow>(SQL.insertJob, [
        this.name,
        id,
        name,
        JSON.stringify(data),
        JSON.stringify(opts),
        delay > 0 ? 'delayed' : 'waiting',
        delay / 1000,
        opts.priority ?? 0,
        now,
        delay > 0 ? delay : null,
      ]);
      await tx.query(SQL.notify, [this.name]);
      if (ins.rows[0]) return ins.rows[0];
      const held = (await tx.query<JobRow>(SQL.getJob, [this.name, id]))
        .rows[0];
      if (held === undefined)
        throw new Error(`Job ${id} was neither added nor found`);
      return held;
    });
    return this.wrap(row);
  }

  async getRepeatableJobs(): Promise<PgRepeatableJob[]> {
    const r = await unit(false, (tx) =>
      tx.query<RepeatRow>(SQL.repeats, [this.name]),
    );
    return r.rows.map((x) => ({
      key: x.key,
      name: x.name,
      id: x.job_id,
      endDate: x.end_date_ms === null ? null : Number(x.end_date_ms),
      tz: x.tz,
      pattern: x.pattern,
      every: x.every_ms,
      next: Number(x.next_ms ?? 0),
    }));
  }

  /** Removes the schedule and its queued runs. False when there was no such schedule. */
  async removeRepeatable(
    name: string,
    repeat: NonNullable<PgJobOptions['repeat']>,
    jobId?: string,
  ): Promise<boolean> {
    const key = repeatKey(name, jobId, repeat);
    return unit(true, async (tx) => {
      const gone = await tx.query(SQL.removeRepeat, [this.name, key]);
      await tx.query(SQL.removeRepeatRuns, [this.name, key]);
      return gone.rows.length > 0;
    });
  }

  /** As `removeRepeatable`, for a schedule known only by the key `getRepeatableJobs` returned. */
  async removeRepeatableByKey(key: string): Promise<boolean> {
    return unit(true, async (tx) => {
      const gone = await tx.query(SQL.removeRepeat, [this.name, key]);
      await tx.query(SQL.removeRepeatRuns, [this.name, key]);
      return gone.rows.length > 0;
    });
  }

  /**
   * One interval schedule under a fixed id, whatever ran before: a schedule of the same id with another interval is removed, and one
   * with this interval is left as it is (its run times are aligned to the interval, so adding it again adds no second run).
   */
  async upsertJobScheduler(
    id: string,
    repeat: { every: number },
    template: { name?: string; data?: D } = {},
  ): Promise<void> {
    const name = template.name ?? id;
    for (const r of await this.getRepeatableJobs()) {
      if (r.name === name && r.id === id && Number(r.every) !== repeat.every)
        await this.removeRepeatableByKey(r.key);
    }
    await this.add(name, (template.data ?? {}) as D, { jobId: id, repeat });
  }

  async getJobs(
    types: string | string[],
    _start?: number,
    _end?: number,
    _asc?: boolean,
  ): Promise<PgJob<D>[]> {
    const states = [
      ...new Set(
        (Array.isArray(types) ? types : [types]).flatMap(
          (t) => TYPE_STATES[t] ?? [],
        ),
      ),
    ];
    if (states.length === 0) return [];
    const r = await unit(false, (tx) =>
      tx.query<JobRow>(SQL.getJobs, [this.name, states]),
    );
    return r.rows.map((x) => this.wrap(x));
  }

  getWaiting = (): Promise<PgJob<D>[]> => this.getJobs('waiting');
  getActive = (): Promise<PgJob<D>[]> => this.getJobs('active');
  getCompleted = (): Promise<PgJob<D>[]> => this.getJobs('completed');
  getFailed = (): Promise<PgJob<D>[]> => this.getJobs('failed');
  getDelayed = (): Promise<PgJob<D>[]> => this.getJobs('delayed');

  close(): Promise<void> {
    return Promise.resolve();
  }
}

export interface PgWorkerOptions {
  concurrency?: number;
  /** How long a claim lasts without a heartbeat; a third of it is the heartbeat interval. */
  stalledInterval?: number;
  maxStalledCount?: number;
}

const SAFETY_NET_MS = 300_000;
const POLL_MS = 1000; // without LISTEN

/** Runs the queue's due jobs: emits `completed`, `failed`, `stalled`, `progress` and `error` like a BullMQ Worker. */
export class PgJobWorker<D = Record<string, unknown>> extends EventEmitter {
  private running = true;
  private wake: (() => void) | undefined;
  private woken = false;
  private readonly inFlight = new Map<string, PgJob<D>>();
  private readonly workerId = `${process.pid}-${randomUUID().slice(0, 8)}`;
  private readonly logger = Logger.getInstance({ service: 'PgJobWorker' });
  private listener: PgListener | undefined;
  private loop: Promise<void> | undefined;
  private beat: Promise<void> | undefined;
  private beatWake: (() => void) | undefined;
  private beatTimer: NodeJS.Timeout | undefined;
  private readonly concurrency: number;
  private readonly claimSeconds: number;
  private readonly maxStalled: number;

  constructor(
    private readonly queueName: string,
    private readonly processor: (job: PgJob<D>) => Promise<unknown>,
    options: PgWorkerOptions = {},
    private readonly defaults: PgJobOptions = {},
  ) {
    super();
    this.concurrency = Math.max(1, options.concurrency ?? 1);
    this.claimSeconds = Math.max((options.stalledInterval ?? 30_000) / 1000, 5);
    this.maxStalled = options.maxStalledCount ?? 1;
    void this.start();
  }

  private async start(): Promise<void> {
    try {
      const url = listenUrl();
      if (url) {
        this.listener = new PgListener(
          url,
          SCHED_CHANNEL,
          (q) => q === this.queueName && this.poke(),
          () => {
            this.poke();
          },
          () => {
            this.poke();
          },
        );
        await this.listener.start();
      }
    } catch (e) {
      this.logger.warn('The scheduler could not LISTEN; it will poll', {
        error: (e as Error).message,
      });
    }
    this.loop = this.run();
    this.beat = this.heartbeatLoop();
  }

  private isRunning(): boolean {
    return this.running;
  }

  private poke(): void {
    if (this.wake) this.wake();
    else this.woken = true;
  }

  private async run(): Promise<void> {
    while (this.isRunning()) {
      try {
        await this.recoverStalled();
        const free = this.concurrency - this.inFlight.size;
        if (free > 0) {
          const jobs = await this.claim(free);
          for (const job of jobs) void this.execute(job);
          if (jobs.length === free) continue; // there may be more
        }
        await this.idle();
      } catch (e) {
        if (!this.isRunning()) break;
        this.emit('error', e);
        await new Promise((r) => setTimeout(r, 1000));
      }
    }
  }

  private async idle(): Promise<void> {
    if (this.woken) {
      this.woken = false;
      return;
    }
    let waitMs = POLL_MS;
    if (this.listener?.healthy()) {
      waitMs = SAFETY_NET_MS;
      // every slot busy: nothing can be claimed until one finishes, and finishing wakes this loop, so a due run is no reason to poll
      if (this.inFlight.size < this.concurrency) {
        const r = await unit(false, (tx) =>
          tx.query<{ secs: number | null }>(SQL.nextDue, [this.queueName]),
        );
        const secs = r.rows[0]?.secs;
        if (secs !== null && secs !== undefined)
          waitMs = Math.min(Math.max(secs * 1000, 20) + 20, SAFETY_NET_MS);
      }
    }
    await new Promise<void>((resolve) => {
      const t = setTimeout(resolve, waitMs);
      if (this.woken) {
        // a notification arrived while the next due time was being read
        clearTimeout(t);
        resolve();
        return;
      }
      this.wake = () => {
        clearTimeout(t);
        resolve();
      };
    });
    this.wake = undefined;
    this.woken = false;
  }

  /** Claim up to `n` due runs and, for a run of a schedule, insert the next run in the same transaction. */
  private async claim(n: number): Promise<PgJob<D>[]> {
    const rows = await unit(true, async (tx) => {
      const claimed = (
        await tx.query<JobRow>(SQL.claim, [
          this.queueName,
          n,
          this.workerId,
          this.claimSeconds,
        ])
      ).rows;
      for (const row of claimed) {
        const rep = row.opts.repeat;
        if (!rep?.key) continue;
        // From the later of the database's clock and the run's own time (as BullMQ does with the previous run): a Node clock that lags
        // the database's must not compute the run just claimed again, and a schedule must never end silently
        const dbNow = Number(row.db_now_ms);
        const next = nextOf(rep, Math.max(dbNow, Number(row.run_ms)));
        if (next === undefined) {
          if (rep.endDate === undefined)
            this.logger.warn(
              'A repeating run was claimed but its schedule gives no next run time',
              {
                queue: this.queueName,
                key: rep.key,
              },
            );
          continue;
        }
        const o = { ...row.opts, delay: undefined };
        await tx.query(SQL.nextRun, [
          this.queueName,
          runId(rep.key, next),
          row.name,
          JSON.stringify(row.data),
          JSON.stringify(o),
          next,
          row.opts.priority ?? 0,
          dbNow,
          Math.max(next - dbNow, 0),
          rep.key,
        ]);
        await tx.query(SQL.bumpRepeat, [this.queueName, rep.key, next]);
      }
      return claimed;
    });
    return rows.map((r) => {
      const job = new PgJob<D>(this.queueName, r);
      job.onProgress = (j, p) => this.emit('progress', j, p);
      return job;
    });
  }

  private async execute(job: PgJob<D>): Promise<void> {
    this.inFlight.set(job.id, job);
    this.beatWake?.();
    const key = [this.queueName, job.id, job.attemptsMade];
    try {
      await this.processor(job);
    } catch (error) {
      try {
        await this.onProcessorError(job, key, error);
      } finally {
        this.inFlight.delete(job.id);
        this.poke();
      }
      return;
    }
    try {
      const done = await this.complete(job, key);
      if (done) this.emit('completed', job, undefined);
    } catch (e) {
      // the run succeeded; a failed completion write must not retry it or mark it failed. The claim lapses and stalled-job
      // recovery decides, as BullMQ does when moveToCompleted fails
      this.emit('error', e);
    } finally {
      this.inFlight.delete(job.id);
      this.poke();
    }
  }

  private complete(job: PgJob<D>, key: (string | number)[]): Promise<boolean> {
    return unit(true, async (tx) => {
      const r = await tx.query(SQL.complete, [
        ...key,
        Date.now(),
        JSON.stringify(100),
      ]);
      if (r.rows.length > 0)
        await this.trim(
          tx,
          'completed',
          job.opts.removeOnComplete ?? this.defaults.removeOnComplete,
          job.id,
        );
      return r.rows.length > 0;
    });
  }

  private async onProcessorError(
    job: PgJob<D>,
    key: (string | number)[],
    error: unknown,
  ): Promise<void> {
    {
      const err = error instanceof Error ? error : new Error(String(error));
      const attempts = job.opts.attempts ?? this.defaults.attempts ?? 1;
      const backoff = job.opts.backoff ?? this.defaults.backoff;
      try {
        if (job.attemptsMade < attempts) {
          const base = backoff?.delay ?? 0;
          const secs =
            (backoff?.type === 'exponential'
              ? base * 2 ** (job.attemptsMade - 1)
              : base) / 1000;
          await unit(true, (tx) =>
            tx.query(SQL.retry, [...key, err.message, secs]),
          );
          this.poke();
        } else {
          await unit(true, async (tx) => {
            const r = await tx.query(SQL.fail, [
              ...key,
              err.message,
              Date.now(),
            ]);
            if (r.rows.length > 0)
              await this.trim(
                tx,
                'failed',
                job.opts.removeOnFail ?? this.defaults.removeOnFail,
                job.id,
              );
          });
        }
      } catch (e) {
        this.emit('error', e);
      }
      this.emit('failed', job, err);
    }
  }

  private async trim(
    tx: Tx,
    state: string,
    keep: number | boolean | undefined,
    jobId: string,
  ): Promise<void> {
    if (typeof keep === 'number')
      await tx.query(SQL.trim, [this.queueName, state, keep]);
    else if (keep === true)
      // BullMQ's removeOnComplete: true drops this job only, not the ones that finished before it
      await tx.query(SQL.dropOne, [this.queueName, jobId, state]);
  }

  private async recoverStalled(): Promise<void> {
    const out = await unit(true, async (tx) => {
      const back = await tx.query<{ id: string }>(SQL.stalledBack, [
        this.queueName,
        this.maxStalled,
      ]);
      const failed = await tx.query<{ id: string }>(SQL.stalledFail, [
        this.queueName,
        this.maxStalled,
        Date.now(),
      ]);
      return [...back.rows, ...failed.rows];
    });
    for (const r of out) this.emit('stalled', r.id);
  }

  private async heartbeatLoop(): Promise<void> {
    while (this.isRunning()) {
      await new Promise<void>((resolve) => {
        // nothing to keep alive: sleep until a run starts
        const ms =
          this.inFlight.size > 0
            ? (this.claimSeconds * 1000) / 3
            : SAFETY_NET_MS;
        this.beatTimer = setTimeout(resolve, ms);
        this.beatWake = () => {
          clearTimeout(this.beatTimer);
          resolve();
        };
      });
      if (!this.isRunning() || this.inFlight.size === 0) continue;
      try {
        await unit(true, (tx) =>
          tx.query(SQL.extend, [
            this.queueName,
            [...this.inFlight.keys()],
            this.claimSeconds,
          ]),
        );
      } catch (e) {
        this.logger.warn('Could not extend job claims', {
          error: (e as Error).message,
        });
      }
    }
  }

  async close(): Promise<void> {
    this.running = false;
    this.poke();
    this.beatWake?.();
    await this.loop?.catch(() => undefined);
    await this.beat?.catch(() => undefined);
    await this.listener?.stop();
    // runs already started finish; their claim simply lapses if the process is going away
    const end = Date.now() + 10_000;
    while (this.inFlight.size > 0 && Date.now() < end)
      await new Promise((r) => setTimeout(r, 50));
  }
}
