import {
  MAX_MUTED_SCHEDULES,
  type InboxPreferences,
} from '../../../../modules/agent-harness/domain/inbox';
import type { InboxStore } from '../../../../modules/agent-harness/ports/inbox';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import { rowRegion, type PgKernelTx } from '../pg-documents/pg';
import {
  pauseFields,
  preferencesOf,
  type StoredInboxPreferences,
} from './records';

const REASON = 'inbox';
/** Claims older than this are pruned as new ones are made (Mongo's TTL index does the same). */
const CLAIM_RETENTION_MS = 30 * 24 * 3600 * 1000;
/** Rows one prune removes at most: a claim never waits on a large delete. */
const PRUNE_BATCH = 100;

interface PreferencesRow {
  muted_schedules: string[] | null;
  paused_until: string | null;
  paused_until_resumed: boolean;
  run_notice_email: boolean;
  run_notice_slack: boolean;
}

const stored = (
  row: PreferencesRow | undefined,
): StoredInboxPreferences | null =>
  row === undefined
    ? null
    : {
        mutedSchedules: row.muted_schedules,
        pausedUntil: row.paused_until,
        pausedUntilResumed: row.paused_until_resumed,
        runNoticeEmail: row.run_notice_email,
        runNoticeSlack: row.run_notice_slack,
      };

const COLUMNS =
  'muted_schedules, paused_until, paused_until_resumed, run_notice_email, run_notice_slack';

/** `InboxStore` on PostgreSQL (`harness.inbox_preferences`, `run_end_notices`, `run_watchers`), each call as its org. */
export class PgInboxStore implements InboxStore {
  preferences(orgId: string, userId: string): Promise<InboxPreferences> {
    return this.unit(orgId, false, async (k) => {
      const { rows } = await k.query<PreferencesRow>(
        `/* pipeshub:harness.inbox_preferences_get */ SELECT ${COLUMNS} FROM harness.inbox_preferences
          WHERE org_id = $1 AND user_id = $2`,
        [orgId, userId],
      );
      return preferencesOf(stored(rows[0]));
    });
  }

  setMuted(
    orgId: string,
    userId: string,
    scheduleId: string,
    muted: boolean,
    now: number,
  ): Promise<InboxPreferences> {
    // The muted list after the change, oldest dropped past the bound, in one statement: two replicas muting at once
    // never lose each other's change.
    const next = muted
      ? `(SELECT coalesce(array_agg(s ORDER BY n), '{}') FROM (
           SELECT s, n FROM unnest(array_append(array_remove(p.muted_schedules, $3), $3)) WITH ORDINALITY AS u(s, n)
           ORDER BY n DESC LIMIT ${MAX_MUTED_SCHEDULES}) kept)`
      : 'array_remove(p.muted_schedules, $3)';
    return this.upsert(
      orgId,
      userId,
      now,
      `muted_schedules = ${next}`,
      muted ? [scheduleId] : [],
      [scheduleId],
    );
  }

  setPause(
    orgId: string,
    userId: string,
    pause: InboxPreferences['pause'],
    now: number,
  ): Promise<InboxPreferences> {
    const fields = pauseFields(pause);
    return this.upsert(
      orgId,
      userId,
      now,
      'paused_until = $3, paused_until_resumed = $4',
      [],
      [fields.pausedUntil, fields.pausedUntilResumed],
      { paused_until: '$3', paused_until_resumed: '$4' },
    );
  }

  setRunNotices(
    orgId: string,
    userId: string,
    channels: Partial<InboxPreferences['runNotices']>,
    now: number,
  ): Promise<InboxPreferences> {
    return this.upsert(
      orgId,
      userId,
      now,
      'run_notice_email = coalesce($3, p.run_notice_email), run_notice_slack = coalesce($4, p.run_notice_slack)',
      [],
      [channels.email ?? null, channels.slack ?? null],
      {
        run_notice_email: 'coalesce($3, false)',
        run_notice_slack: 'coalesce($4, false)',
      },
    );
  }

  claimRunNotice(
    orgId: string,
    runId: string,
    userId: string,
    now: number,
  ): Promise<boolean> {
    return this.unit(orgId, true, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.run_end_notices_claim */ INSERT INTO harness.run_end_notices (org_id, run_id, user_id, created_at, region)
         VALUES ($1, $2, $3, $4, $5) ON CONFLICT (org_id, run_id) DO NOTHING`,
        [orgId, runId, userId, now, rowRegion()],
      );
      await k.query(
        `/* pipeshub:harness.run_end_notices_prune */ DELETE FROM harness.run_end_notices
          WHERE org_id = $1 AND (org_id, run_id) IN (
            SELECT org_id, run_id FROM harness.run_end_notices WHERE org_id = $1 AND created_at < $2 LIMIT ${PRUNE_BATCH})`,
        [orgId, now - CLAIM_RETENTION_MS],
      );
      return rowCount === 1;
    });
  }

  async watch(lease: {
    orgId: string;
    userId: string;
    sessionId: string;
    watcherId: string;
    until: number;
  }): Promise<void> {
    await this.unit(lease.orgId, true, (k) =>
      k.query(
        `/* pipeshub:harness.run_watchers_watch */ INSERT INTO harness.run_watchers (org_id, watcher_id, user_id, session_id, until, region)
         VALUES ($1, $2, $3, $4, $5, $6)
         ON CONFLICT (org_id, watcher_id) DO UPDATE SET user_id = EXCLUDED.user_id, session_id = EXCLUDED.session_id,
           until = EXCLUDED.until`,
        [
          lease.orgId,
          lease.watcherId,
          lease.userId,
          lease.sessionId,
          lease.until,
          rowRegion(),
        ],
      ),
    );
  }

  async unwatch(orgId: string, watcherId: string): Promise<void> {
    await this.unit(orgId, true, (k) =>
      k.query(
        `/* pipeshub:harness.run_watchers_unwatch */ DELETE FROM harness.run_watchers WHERE org_id = $1 AND watcher_id = $2`,
        [orgId, watcherId],
      ),
    );
  }

  watching(
    orgId: string,
    userId: string,
    sessionId: string,
    at: number,
  ): Promise<boolean> {
    return this.unit(orgId, true, async (k) => {
      // A lease no replica renewed (its process died) is dropped by the next read of that person's conversation.
      await k.query(
        `/* pipeshub:harness.run_watchers_prune */ DELETE FROM harness.run_watchers
          WHERE org_id = $1 AND user_id = $2 AND session_id = $3 AND until <= $4`,
        [orgId, userId, sessionId, at],
      );
      const { rows } = await k.query<{ live: boolean }>(
        `/* pipeshub:harness.run_watchers_watching */ SELECT EXISTS (SELECT 1 FROM harness.run_watchers
          WHERE org_id = $1 AND user_id = $2 AND session_id = $3 AND until > $4) AS live`,
        [orgId, userId, sessionId, at],
      );
      return rows[0]?.live === true;
    });
  }

  async eraseUser(orgId: string, userId: string): Promise<void> {
    await this.unit(orgId, true, async (k) => {
      for (const table of [
        'inbox_preferences',
        'run_end_notices',
        'run_watchers',
      ]) {
        await k.query(
          `/* pipeshub:harness.inbox_erase_user */ DELETE FROM harness.${table} WHERE org_id = $1 AND user_id = $2`,
          [orgId, userId],
        );
      }
    });
  }

  async eraseOrg(orgId: string): Promise<void> {
    await this.unit(orgId, true, async (k) => {
      for (const table of [
        'inbox_preferences',
        'run_end_notices',
        'run_watchers',
      ]) {
        await k.query(
          `/* pipeshub:harness.inbox_erase_org */ DELETE FROM harness.${table} WHERE org_id = $1`,
          [orgId],
        );
      }
    });
  }

  private unit<T>(
    orgId: string,
    write: boolean,
    fn: (k: PgKernelTx) => Promise<T>,
  ): Promise<T> {
    return orgOrJobsUnit(undefined, orgId, REASON, { write }, fn);
  }

  /**
   * Inserts the person's row with `insert` (defaults elsewhere) or applies `set` to the existing one, in one
   * statement. `$1` org, `$2` user, then `params` from `$3`; `insertMuted` is the muted list a new row starts with.
   */
  private upsert(
    orgId: string,
    userId: string,
    now: number,
    set: string,
    insertMuted: string[],
    params: unknown[],
    insert: Record<string, string> = {},
  ): Promise<InboxPreferences> {
    const at = params.length + 3;
    const columns = [
      'paused_until',
      'paused_until_resumed',
      'run_notice_email',
      'run_notice_slack',
    ];
    const defaults: Record<string, string> = {
      paused_until: 'NULL',
      paused_until_resumed: 'false',
      run_notice_email: 'false',
      run_notice_slack: 'false',
      ...insert,
    };
    return this.unit(orgId, true, async (k) => {
      const { rows } = await k.query<PreferencesRow>(
        `/* pipeshub:harness.inbox_preferences_put */ INSERT INTO harness.inbox_preferences AS p
           (org_id, user_id, muted_schedules, ${columns.join(', ')}, updated_at, region)
         VALUES ($1, $2, $${at}::text[], ${columns.map((c) => defaults[c]).join(', ')}, $${at + 1}, $${at + 2})
         ON CONFLICT (org_id, user_id) DO UPDATE SET ${set}, updated_at = $${at + 1}
         RETURNING ${COLUMNS}`,
        [orgId, userId, ...params, insertMuted, now, rowRegion()],
      );
      return preferencesOf(stored(rows[0]));
    });
  }
}
