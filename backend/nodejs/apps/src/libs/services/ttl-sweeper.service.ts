import { Logger } from './logger.service';
import { spawnDetached, system } from '../context/orgContext';
import { Tx, withJobsUow } from '../db';
import { docstoreMetrics } from '../repositories/metrics';
import { retentionCutoff } from '../../modules/notification/utils/notification-api.utils';

const DEFAULT_INTERVAL_MS = 60_000;
const BATCH = 5000;
const PUBLISHED_OUTBOX_KEEP_MS = 7 * 24 * 3600_000;

interface Target {
  name: string;
  /** Deletes up to `$2` rows past the cutoff `$1`; the count of rows deleted is the row count. */
  sql: string;
  cutoff: (now: Date) => Date;
}

// Rows of an unpartitioned table are found by ctid; a partitioned table has no usable ctid, so its primary key stands in.
const TARGETS: Target[] = [
  {
    name: 'notifications',
    sql: `/* pipeshub:platform.ttl_sweep_notifications */ DELETE FROM platform.notifications WHERE (org_id, created_at, id) IN
            (SELECT org_id, created_at, id FROM platform.notifications WHERE created_at < $1 LIMIT $2)`,
    cutoff: (now) => retentionCutoff(now),
  },
  {
    name: 'outbox',
    sql: `/* pipeshub:platform.ttl_sweep_outbox */ DELETE FROM platform.outbox
            WHERE ctid IN (SELECT ctid FROM platform.outbox WHERE status = 'published' AND published_at < $1 LIMIT $2)`,
    cutoff: (now) => new Date(now.getTime() - PUBLISHED_OUTBOX_KEEP_MS),
  },
];

/**
 * Removes what the document store no longer shows: notifications past retention and delivered outbox rows (the work Mongo's
 * TTL indexes did). Runs as the jobs role across every org, in batches so no pass holds a long lock.
 */
export class TtlSweeper {
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private readonly logger: Logger,
    private readonly intervalMs: number = DEFAULT_INTERVAL_MS,
    private readonly clock: () => Date = () => new Date(),
  ) {}

  start(): void {
    if (this.timer) return;
    // the timer outlives the caller's context: a tick must not carry whichever org was bound when it was created
    this.timer = spawnDetached(() =>
      setInterval(() => void this.sweepOnce(), this.intervalMs),
    );
    this.timer.unref();
    this.logger.info('TTL sweeper started', { intervalMs: this.intervalMs });
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    while (this.running) await new Promise((r) => setTimeout(r, 25));
  }

  /** One pass over every target; a target that fails is logged and does not stop the others. Returns rows removed per target. */
  async sweepOnce(): Promise<Record<string, number>> {
    if (this.running) return {};
    this.running = true;
    const removed: Record<string, number> = {};
    try {
      const now = this.clock();
      for (const t of TARGETS) {
        await this.guarded(t.name, removed, async () => {
          let n: number;
          let total = 0;
          do {
            n = await this.inJobs(
              async (k) =>
                (await k.query(t.sql, [t.cutoff(now), BATCH])).rowCount ?? 0,
            );
            total += n;
          } while (n === BATCH);
          return total;
        });
      }
      const total = Object.values(removed).reduce((a, b) => a + b, 0);
      if (total > 0) this.logger.info('TTL sweep removed rows', removed);
    } finally {
      this.running = false;
    }
    return removed;
  }

  private async guarded(
    name: string,
    removed: Record<string, number>,
    run: () => Promise<number>,
  ): Promise<void> {
    try {
      removed[name] = await run();
      if (removed[name])
        docstoreMetrics.ttlSwept.inc({ target: name }, removed[name]);
    } catch (error) {
      docstoreMetrics.ttlSweepFailed.inc({ target: name });
      this.logger.error('TTL sweep failed', {
        target: name,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  private inJobs<T>(fn: (k: Tx) => Promise<T>): Promise<T> {
    return system('ttl-sweeper', () => withJobsUow({ write: true }, fn));
  }
}
