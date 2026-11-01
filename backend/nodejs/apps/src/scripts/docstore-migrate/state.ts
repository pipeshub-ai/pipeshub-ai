/**
 * Migration state lives in `platform.jobs` (kind `docstore_migration`): one running job per install, progress as JSON. The migrate
 * login starts and ends the job and reads it back; the jobs login saves progress. Every command can be run again: it attaches to
 * the running job and continues from the checkpoints in `progress`.
 */
import type { MigrationRuntime } from './runtime';

export const JOB_KIND = 'docstore_migration';

export interface CollectionProgress {
  state: 'pending' | 'running' | 'done';
  /** Last `_id` copied (hex). With `ranges`, each range keeps its own and this one stays null until all are done. */
  last: string | null;
  /** A big collection copied by parallel workers: `_id` ranges (`from` exclusive, `to` inclusive), each with its own checkpoint. */
  ranges?: Array<{
    from: string | null;
    to: string | null;
    last: string | null;
    done: boolean;
  }>;
  copied: number;
  skipped: number;
  /** The first documents the copy left out (id to reason), so that the verifier lists them instead of reporting them missing. */
  skippedIds?: Record<string, string>;
  /** Rows written per table (cumulative; rows found already present are not counted). */
  rows: Record<string, number>;
}

export interface Progress {
  version: 1;
  phase: string;
  /** What the operator asked for, so a restart does the same: `offline`/`online`. */
  mode?: 'offline' | 'online';
  collections: Record<string, CollectionProgress>;
  schema?: { appliedAt: string };
  postLoad?: { appliedAt: string };
  preflight?: { at: string; ok: boolean; red: number };
  /** The reverse follower after the flip: the last change-log id applied, how many rows, and how far behind the last one was. */
  reverse?: {
    /** the last transaction id whose log rows were written back */ cursor: string;
    applied: number;
    /** Behind PostgreSQL by this much at the last batch; `alarm` is set above the alarm limit (`status` shows both). */
    lagMs: number;
    alarm?: boolean;
  };
  follow?: {
    resumeToken: unknown;
    appliedUntil: string | null;
    events: number;
    /** Behind MongoDB by this much at the last batch; `alarm` is set above the alarm limit. */
    lagMs?: number;
    alarm?: boolean;
    /** Times the token had fallen out of the oplog and the copy was brought level by comparing. */
    resyncs?: number;
  };
  /** `token`: the follower's position when the last clean verify pass began; the next incremental pass re-checks what changed after it. */
  verify?: { at: string; ok: boolean; diffs: number; token?: unknown };
  drain?: { at: string; stoppedRuns: number };
  outbox?: { copied: Record<string, number> };
  flip?: { at: string; backend: 'postgres' };
  [k: string]: unknown;
}

/** JSON with the keys in a fixed order: PostgreSQL's jsonb hands them back in its own, so two texts of one value must compare equal. */
export const canon = (v: unknown): string =>
  JSON.stringify(v, (_k, x: unknown) =>
    x && typeof x === 'object' && !Array.isArray(x)
      ? Object.fromEntries(
          Object.entries(x).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
        )
      : x,
  );

export const emptyProgress = (): Progress => ({
  version: 1,
  phase: 'new',
  collections: {},
});

/** Progress as stored: a JSON object whose `collections` is an object too. Anything else is refused, not merged into a fresh state. */
function toProgress(raw: unknown): Progress {
  const isObj = (x: unknown): boolean =>
    typeof x === 'object' && x !== null && !Array.isArray(x);
  if (raw === null || raw === undefined) return emptyProgress();
  const p = isObj(raw)
    ? { ...emptyProgress(), ...(raw as Partial<Progress>) }
    : null;
  if (!p || !isObj(p.collections))
    throw new Error('platform.jobs.progress is not a valid migration state');
  return p;
}

export class JobState {
  private constructor(
    private readonly rt: MigrationRuntime,
    readonly id: number,
    public progress: Progress,
  ) {}

  /** The running job, or null. */
  static async current(
    rt: MigrationRuntime,
  ): Promise<{ id: number; progress: Progress; status: string } | null> {
    const { rows } = await rt.migratePool.query<{
      id: string;
      status: string;
      progress: Progress | null;
    }>(
      'SELECT id::text AS id, status, progress FROM platform.jobs WHERE kind = $1 AND status = $2 ORDER BY id DESC LIMIT 1',
      [JOB_KIND, 'running'],
    );
    if (!rows[0]) return null;
    return {
      id: Number(rows[0].id),
      status: rows[0].status,
      progress: toProgress(rows[0].progress),
    };
  }

  /** The most recent job of the kind, running or not (for `status`). */
  static async latest(
    rt: MigrationRuntime,
  ): Promise<{ id: number; progress: Progress; status: string } | null> {
    const { rows } = await rt.migratePool.query<{
      id: string;
      status: string;
      progress: Progress | null;
    }>(
      'SELECT id::text AS id, status, progress FROM platform.jobs WHERE kind = $1 ORDER BY id DESC LIMIT 1',
      [JOB_KIND],
    );
    if (!rows[0]) return null;
    return {
      id: Number(rows[0].id),
      status: rows[0].status,
      progress: toProgress(rows[0].progress),
    };
  }

  /** Attaches to the running job or starts one. */
  static async attach(rt: MigrationRuntime): Promise<JobState> {
    const cur = await JobState.current(rt);
    if (cur) {
      const js = new JobState(rt, cur.id, cur.progress);
      js.remember(cur.progress);
      return js;
    }
    const { rows } = await rt.migratePool.query<{ id: string }>(
      'SELECT platform.job_start($1)::text AS id',
      [JOB_KIND],
    );
    const js = new JobState(rt, Number(rows[0]?.id), emptyProgress());
    await js.checkpoint();
    return js;
  }

  private saving: Promise<void> = Promise.resolve();

  /**
   * Saves the progress (jobs login). Parallel workers share one progress object, so saves are queued: each one writes the state as it
   * is when its turn comes, and an older snapshot can never land after a newer one.
   */
  checkpoint(): Promise<void> {
    const next = this.saving.then(() => this.persist());
    this.saving = next.catch(() => undefined);
    return next;
  }

  /** The progress as each top-level key was last read from or written to the database, to tell what this process changed. */
  private seen = new Map<string, string>();

  private remember(p: Progress): void {
    this.seen = new Map(Object.entries(p).map(([k, v]) => [k, canon(v)]));
  }

  /**
   * Another command may be saving too (the follower runs while a verify or the cut-over does): what this process changed since it
   * last read is laid over what the database holds now, and what only the other changed is kept. (Two processes changing the same key
   * still race; the procedures do not do that.)
   */
  private async merged(): Promise<Progress> {
    const { rows } = await this.rt.migratePool.query<{
      progress: Progress | null;
    }>('SELECT progress FROM platform.jobs WHERE id = $1::bigint', [this.id]);
    const theirs: Progress = toProgress(rows[0]?.progress);
    for (const [k, v] of Object.entries(this.progress))
      if (canon(v) !== this.seen.get(k))
        (theirs as Record<string, unknown>)[k] = v;
    // adopt what only the other process changed; a key it did not change keeps this process's own object, because running code holds
    // references into it (a collection's progress, a range) and a copy would orphan them
    for (const k of Object.keys(theirs)) {
      const mine = canon((this.progress as Record<string, unknown>)[k]);
      const theirsNow = canon((theirs as Record<string, unknown>)[k]);
      if (mine === this.seen.get(k) && theirsNow !== this.seen.get(k))
        (this.progress as Record<string, unknown>)[k] = (
          theirs as Record<string, unknown>
        )[k];
    }
    return theirs;
  }

  private async persist(): Promise<void> {
    const text = JSON.stringify(await this.merged());
    await this.rt.jobs(async (_tx, k) => {
      const { rows } = await k.query<{ ok: boolean }>(
        '/* pipeshub:platform.job_checkpoint */ SELECT platform.job_checkpoint($1::bigint, $2::text) AS ok',
        [this.id, text],
      );
      if (!rows[0]?.ok)
        throw new Error(`job ${String(this.id)} is no longer running`);
    });
    this.remember(JSON.parse(text) as Progress);
  }

  async finish(status: 'done' | 'failed' | 'cancelled'): Promise<void> {
    await this.rt.migratePool.query(
      'SELECT platform.job_finish($1::bigint, $2::text)',
      [this.id, status],
    );
  }
}
