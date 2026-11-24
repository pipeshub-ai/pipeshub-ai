import { Logger } from './logger.service';
import { spawnDetached, system } from '../context/orgContext';
import { Tx, withJobsUow } from '../db';
import { docstoreMetrics } from '../repositories/metrics';
import { AGENT_CALL_EVENT_RETENTION_DAYS } from '../repositories/agent-call-event/types';
import { retentionCutoff } from '../../modules/notification/utils/notification-api.utils';
import { trimActivitiesPage } from '../repositories/activity/pg';
import { replayWindowSeconds } from '../context/envelope';

const DEFAULT_INTERVAL_MS = 60_000;
const BATCH = 5000;
const HOUR_MS = 3600_000;
const PUBLISHED_OUTBOX_KEEP_MS = 7 * 24 * HOUR_MS;
const DELEGATION_EVENTS_KEEP_MS = 400 * 24 * HOUR_MS;
const AGENT_CALL_EVENTS_KEEP_MS =
  AGENT_CALL_EVENT_RETENTION_DAYS * 24 * HOUR_MS;
// Sign-in history is read only through each person's newest row of a type, which the trim keeps; the rest is history no screen
// shows. 90 days is three times the longest default token lifetime (REFRESH_TOKEN_EXPIRY, EMAIL_VERIFIED_TOKEN_EXPIRY: 30 days).
const DEFAULT_ACTIVITY_RETENTION_DAYS = 90;
// a pass reads every activity row of every organisation once, so it runs daily rather than on every tick
const ACTIVITY_SWEEP_EVERY_MS = 24 * HOUR_MS;

/** `USER_ACTIVITY_RETENTION_DAYS`: whole days of sign-in history to keep (default 90); anything else stops the start. */
export function activityRetentionDays(
  raw = process.env.USER_ACTIVITY_RETENTION_DAYS,
): number {
  if (raw === undefined || raw.trim() === '')
    return DEFAULT_ACTIVITY_RETENTION_DAYS;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1)
    throw new Error(
      `USER_ACTIVITY_RETENTION_DAYS must be a whole number of days, at least 1 (got ${JSON.stringify(raw)})`,
    );
  return n;
}

// a row stays an hour past its expiry so that a caller can still say "expired" rather than "unknown"
const expiredAnHourAgo = (now: Date): Date => new Date(now.getTime() - HOUR_MS);

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
  {
    name: 'access_tokens',
    sql: `/* pipeshub:oauth.ttl_sweep_access_tokens */ DELETE FROM oauth.access_tokens
            WHERE ctid IN (SELECT ctid FROM oauth.access_tokens WHERE expires_at < $1 LIMIT $2)`,
    cutoff: expiredAnHourAgo,
  },
  {
    name: 'refresh_tokens',
    sql: `/* pipeshub:oauth.ttl_sweep_refresh_tokens */ DELETE FROM oauth.refresh_tokens
            WHERE ctid IN (SELECT ctid FROM oauth.refresh_tokens WHERE expires_at < $1 LIMIT $2)`,
    cutoff: expiredAnHourAgo,
  },
  {
    name: 'authorization_codes',
    sql: `/* pipeshub:oauth.ttl_sweep_authorization_codes */ DELETE FROM oauth.authorization_codes
            WHERE ctid IN (SELECT ctid FROM oauth.authorization_codes WHERE expires_at < $1 LIMIT $2)`,
    cutoff: expiredAnHourAgo,
  },
  {
    name: 'device_authorizations',
    sql: `/* pipeshub:oauth.ttl_sweep_device_authorizations */ DELETE FROM oauth.device_authorizations
            WHERE ctid IN (SELECT ctid FROM oauth.device_authorizations WHERE expires_at < $1 LIMIT $2)`,
    cutoff: expiredAnHourAgo,
  },
  {
    name: 'agent_registrations',
    sql: `/* pipeshub:oauth.ttl_sweep_agent_registrations */ DELETE FROM oauth.agent_registrations
            WHERE ctid IN (SELECT ctid FROM oauth.agent_registrations WHERE purge_at < $1 LIMIT $2)`,
    cutoff: (now) => now,
  },
  {
    name: 'delegation_events',
    sql: `/* pipeshub:oauth.ttl_sweep_delegation_events */ DELETE FROM oauth.delegation_events
            WHERE ctid IN (SELECT ctid FROM oauth.delegation_events WHERE created_at < $1 LIMIT $2)`,
    cutoff: (now) => new Date(now.getTime() - DELEGATION_EVENTS_KEEP_MS),
  },
  {
    name: 'agent_call_events',
    sql: `/* pipeshub:oauth.ttl_sweep_agent_call_events */ DELETE FROM oauth.agent_call_events
            WHERE ctid IN (SELECT ctid FROM oauth.agent_call_events WHERE created_at < $1 LIMIT $2)`,
    cutoff: (now) => new Date(now.getTime() - AGENT_CALL_EVENTS_KEEP_MS),
  },
  {
    name: 'consumer_inbox',
    sql: `/* pipeshub:platform.ttl_sweep_consumer_inbox */ DELETE FROM platform.consumer_inbox
            WHERE ctid IN (SELECT ctid FROM platform.consumer_inbox WHERE received_at < $1 LIMIT $2)`,
    cutoff: (now) => new Date(now.getTime() - replayWindowSeconds() * 1000),
  },
];

/**
 * Removes what the document store no longer shows: notifications past retention, delivered outbox rows, expired OAuth tokens,
 * codes and device authorizations, delegation events past their retention (the work Mongo's TTL indexes did), and consumer inbox
 * rows past the replay window. Runs as the jobs role across every org, in batches so no pass holds a long lock.
 * Once a day it also trims each live organisation's sign-in history (`identity.user_activities`) as that organisation: the jobs role
 * holds no privilege on identity tables, and only lists the organisations.
 */
export class TtlSweeper {
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private nextActivitySweepMs = 0;
  private readonly activityKeepMs: number;

  constructor(
    private readonly logger: Logger,
    private readonly intervalMs: number = DEFAULT_INTERVAL_MS,
    private readonly clock: () => Date = () => new Date(),
    activityDays: number = activityRetentionDays(),
  ) {
    this.activityKeepMs = activityDays * 24 * HOUR_MS;
  }

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
      if (now.getTime() >= this.nextActivitySweepMs) {
        // the day's slot is taken only by a trim that finished: a failed one (a lock timeout, a blip) stays due for the next tick
        await this.guarded('user_activities', removed, async () => {
          const n = await this.trimActivities(
            new Date(now.getTime() - this.activityKeepMs),
          );
          this.nextActivitySweepMs = now.getTime() + ACTIVITY_SWEEP_EVERY_MS;
          return n;
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

  private async trimActivities(cutoff: Date): Promise<number> {
    const orgs = await this.inJobs(
      async (k) =>
        (
          await k.query<{ org_id: string }>(
            '/* pipeshub:identity.activity_trim_orgs */ SELECT org_id FROM identity.gauge_live_orgs()',
          )
        ).rows,
    );
    let total = 0;
    let failed = 0;
    // one organisation that fails must not keep the ones after it from being trimmed
    for (const { org_id: org } of orgs) {
      try {
        let after = '';
        for (;;) {
          const page = await trimActivitiesPage(org, after, cutoff, BATCH);
          total += page.removed;
          if (page.last === null) break;
          after = page.last;
        }
      } catch (error) {
        failed += 1;
        this.logger.error('Sign-in history trim failed for an organisation', {
          orgId: org,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    if (failed > 0)
      throw new Error(
        `${String(failed)} of ${String(orgs.length)} organisations failed (${String(total)} rows removed)`,
      );
    return total;
  }

  private inJobs<T>(fn: (k: Tx) => Promise<T>): Promise<T> {
    return system('ttl-sweeper', () => withJobsUow({ write: true }, fn));
  }
}
