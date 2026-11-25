import { bindOrgContext, spawnDetached } from '../../../context/orgContext';
import {
  HarnessLogger,
  harnessLogMeta,
  silentHarnessLogger,
} from '../../../../modules/agent-harness/application/harness-log';
import {
  JournalTail,
  JournalTailSubscription,
} from '../../../../modules/agent-harness/ports/journal-tail';
import { jobsUnit } from '../pg-documents/pg';

/** How often the watched runs' journals are asked for their last seq. */
export const TAIL_POLL_MS = 250;

/** The last seq of each watched run, as one read (`session_events_by_run`), deployment-wide as the jobs role. */
export type ReadLastSeqs = (
  runs: ReadonlyArray<{ orgId: string; runId: string }>,
) => Promise<Map<string, number>>;

const keyOf = (orgId: string, runId: string): string => `${orgId}:${runId}`;

const readLastSeqs: ReadLastSeqs = (runs) =>
  jobsUnit('agent-harness.journal-tail', { write: false }, async (k) => {
    const { rows } = await k.query<{
      org_id: string;
      run_id: string;
      seq: string | null;
    }>(
      `/* pipeshub:harness.session_events_tail */ SELECT w.org_id, w.run_id,
         (SELECT e.seq FROM harness.session_events e WHERE e.org_id = w.org_id AND e.run_id = w.run_id
           ORDER BY e.seq DESC LIMIT 1)::text AS seq
       FROM unnest($1::text[], $2::text[]) AS w(org_id, run_id)`,
      [runs.map((run) => run.orgId), runs.map((run) => run.runId)],
    );
    return new Map(
      rows.map((row) => [
        keyOf(row.org_id, row.run_id),
        row.seq === null ? 0 : Number(row.seq),
      ]),
    );
  });

/**
 * Cross-replica journal tail on PostgreSQL (REPORT 6.2.4). PgBouncer in transaction mode keeps no session for LISTEN,
 * so one poll per process reads the last seq of every run a stream watches, in one statement on the journal's
 * `(org_id, run_id, seq)` index, and signals the runs whose journal grew with that seq. A new watcher's first poll
 * tells it the run's last seq: whatever committed between the stream's own read and its watch is read then, never
 * missed.
 */
export class PgJournalTail implements JournalTail {
  private readonly watchers = new Map<
    string,
    {
      orgId: string;
      runId: string;
      callbacks: Set<(seq?: number) => void>;
      /** Watching since the last poll: told the run's last seq once, whether or not it grew. */
      fresh: Set<(seq?: number) => void>;
      seen: number;
    }
  >();
  private timer: NodeJS.Timeout | null = null;
  private failing = false;

  constructor(
    private readonly read: ReadLastSeqs = readLastSeqs,
    private readonly logger: HarnessLogger = silentHarnessLogger,
    private readonly pollMs = TAIL_POLL_MS,
  ) {}

  watch(
    orgId: string,
    runId: string,
    onChange: (seq?: number) => void,
  ): JournalTailSubscription {
    // Told in the context it was registered in (the viewer's request), never in the poller's or publisher's.
    const told = bindOrgContext(onChange);
    const key = keyOf(orgId, runId);
    const entry = this.watchers.get(key) ?? {
      orgId,
      runId,
      callbacks: new Set<(seq?: number) => void>(),
      fresh: new Set<(seq?: number) => void>(),
      seen: 0,
    };
    entry.callbacks.add(told);
    entry.fresh.add(told);
    this.watchers.set(key, entry);
    this.schedule();
    return {
      close: () => {
        entry.callbacks.delete(told);
        entry.fresh.delete(told);
        if (entry.callbacks.size === 0) this.watchers.delete(key);
        if (this.watchers.size === 0) this.stop();
      },
    };
  }

  private schedule(): void {
    if (this.timer !== null || this.watchers.size === 0) return;
    this.timer = spawnDetached(() =>
      setTimeout(() => {
        void this.poll().finally(() => {
          this.timer = null;
          this.schedule();
        });
      }, this.pollMs),
    );
    this.timer.unref();
  }

  /** One read for every watched run; a failed read is logged once and retried at the next tick. */
  async poll(): Promise<void> {
    const entries = [...this.watchers.values()];
    if (entries.length === 0) return;
    let last: Map<string, number>;
    try {
      last = await this.read(entries);
      this.failing = false;
    } catch (error) {
      if (!this.failing) {
        this.failing = true;
        this.logger.warn(
          'projection.tail_degraded',
          harnessLogMeta('projection.tail_degraded', {
            reason: 'journal_poll_failed',
            errorType: error instanceof Error ? error.name : typeof error,
            pollMs: this.pollMs,
          }),
        );
      }
      return;
    }
    for (const entry of entries) {
      const seq = last.get(keyOf(entry.orgId, entry.runId)) ?? 0;
      const fresh = new Set(entry.fresh);
      entry.fresh.clear();
      const grew = seq > entry.seen;
      if (grew) entry.seen = seq;
      if (seq === 0) continue;
      for (const callback of [...entry.callbacks]) {
        if (grew || fresh.has(callback)) callback(seq);
      }
    }
  }

  private stop(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}
