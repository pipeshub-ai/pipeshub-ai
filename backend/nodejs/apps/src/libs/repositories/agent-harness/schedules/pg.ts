import { HarnessScheduleRefusedError } from '../../../../modules/agent-harness/domain/errors';
import {
  type AgentSchedule,
  type AgentVersionPin,
  type EventTriggerKind,
  type FiringOutcome,
  type RunTriggerContext,
  type ScheduleFiring,
  type ScheduleStatus,
  WATCH_GRACE_MS,
} from '../../../../modules/agent-harness/domain/schedules';
import type {
  ScheduleEraser,
  ScheduleLimits,
  ScheduleStore,
} from '../../../../modules/agent-harness/ports/schedules';
import {
  RunInputSeal,
  UNSEALED_RUN_INPUT,
} from '../../../../modules/agent-harness/application/run-input-seal';
import { ambientUnit, crossOrgUnit } from '../pg-documents/ambient';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import {
  decodeDoc,
  docParam,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';
import {
  firingDocument,
  scheduleDocument,
  toFiring,
  toSchedule,
} from './mongo';
import type { ScheduleDocument, ScheduleFiringDocument } from './document';

const REASON = 'schedules';

/** A cron claim whose firing already existed: thrown inside the unit so its cursor move rolls back. */
class SlotTaken extends Error {}

const firingOf = (doc: unknown): ScheduleFiring =>
  toFiring(decodeDoc<ScheduleFiringDocument>(doc));

/**
 * Schedules over PostgreSQL (P8a): `harness.schedules`, `schedule_firings` and `schedule_counters`, each row the
 * document the Mongo store keeps. A cron slot is claimed in one unit that moves the schedule's cursor by a guarded
 * UPDATE (`next_fire_at` equal to what the firer read) and inserts the firing under its unique slot key: of two
 * firers one commits and the other moves nothing. A create locks the org's counter row first, so concurrent creates
 * count in turn and none passes a limit. Stale firings are retaken with `FOR UPDATE SKIP LOCKED` and an attempts
 * guard. The due, stale and watched scans are deployment-wide, as the jobs role.
 */
export class PgScheduleStore implements ScheduleStore, ScheduleEraser {
  /** `inputs` seals the template's query, attachments, filters and caller (GAPS-1); plaintext when omitted. */
  constructor(
    private readonly time: PgTime = pgServerTime,
    private readonly inputs: RunInputSeal = UNSEALED_RUN_INPUT,
  ) {}

  now(): Promise<number> {
    return ambientUnit(REASON, { write: false }, (k) => this.time.now(k));
  }

  async create(
    schedule: AgentSchedule,
    limits: ScheduleLimits,
  ): Promise<AgentSchedule> {
    const sealed = await scheduleDocument(this.inputs, schedule);
    const { orgId, actorId } = schedule;
    await this.org(orgId, true, async (k) => {
      for (const key of [`org:${orgId}`, `actor:${orgId}:${actorId}`]) {
        await k.query(
          `/* pipeshub:harness.schedule_counters_lock */ INSERT INTO harness.schedule_counters AS c
             (org_id, key, count, updated_at, expires_at, region)
           VALUES ($1, $2, 0, $3, NULL, $4)
           ON CONFLICT (org_id, key) DO UPDATE SET updated_at = c.updated_at`,
          [orgId, key, schedule.createdAt, rowRegion()],
        );
      }
      const { rows } = await k.query<{ org: number; actor: number }>(
        `/* pipeshub:harness.schedules_count */ SELECT count(*)::int AS org,
           count(*) FILTER (WHERE actor_id = $2)::int AS actor
           FROM harness.schedules WHERE org_id = $1`,
        [orgId, actorId],
      );
      const org = rows[0]?.org ?? 0;
      const actor = rows[0]?.actor ?? 0;
      if (org >= limits.perOrg || actor >= limits.perActor) {
        throw new HarnessScheduleRefusedError(
          'SCHEDULE_LIMIT',
          'No more schedules may be created',
        );
      }
      await k.query(
        `/* pipeshub:harness.schedule_counters_count */ UPDATE harness.schedule_counters
            SET count = CASE WHEN key = $2 THEN $4::bigint ELSE $5::bigint END, updated_at = $6
          WHERE org_id = $1 AND key IN ($2, $3)`,
        [
          orgId,
          `org:${orgId}`,
          `actor:${orgId}:${actorId}`,
          org + 1,
          actor + 1,
          schedule.createdAt,
        ],
      );
      await k.query(
        `/* pipeshub:harness.schedules_insert */ INSERT INTO harness.schedules (org_id, id, doc, region)
         VALUES ($1, $2, $3::jsonb, $4)`,
        [orgId, schedule.id, docParam(sealed), rowRegion()],
      );
    });
    return structuredClone(schedule);
  }

  async get(orgId: string | null, id: string): Promise<AgentSchedule | null> {
    const read = (k: PgKernelTx): Promise<ScheduleDocument[]> =>
      this.scheduleDocs(
        k,
        `/* pipeshub:harness.schedules_get */ SELECT doc FROM harness.schedules
          WHERE id = $1 AND ($2::text IS NULL OR org_id = $2)`,
        [id, orgId],
      );
    const found =
      orgId === null
        ? await crossOrgUnit(REASON, { write: false }, read)
        : await this.org(orgId, false, read);
    return found[0] === undefined ? null : toSchedule(this.inputs, found[0]);
  }

  async list(
    orgId: string,
    filter: { actorId?: string },
    limit: number,
  ): Promise<AgentSchedule[]> {
    const found = await this.org(orgId, false, (k) =>
      this.scheduleDocs(
        k,
        `/* pipeshub:harness.schedules_list */ SELECT doc FROM harness.schedules
          WHERE org_id = $1 AND ($2::text IS NULL OR actor_id = $2)
          ORDER BY created_at DESC, id LIMIT $3`,
        [orgId, filter.actorId ?? null, limit],
      ),
    );
    return this.open(found);
  }

  async setStatus(
    orgId: string,
    id: string,
    change: {
      from: readonly ScheduleStatus[];
      to: ScheduleStatus;
      reason: string | null;
      nextFireAt: number | null;
    },
    now: number,
  ): Promise<AgentSchedule | null> {
    const found = await this.org(orgId, true, (k) =>
      this.scheduleDocs(
        k,
        `/* pipeshub:harness.schedules_set_status */ UPDATE harness.schedules SET doc = doc || $4::jsonb
          WHERE org_id = $1 AND id = $2 AND status = ANY($3::text[]) RETURNING doc`,
        [
          orgId,
          id,
          [...change.from],
          docParam({
            status: change.to,
            statusReason: change.reason,
            nextFireAt: change.nextFireAt,
            updatedAt: now,
          }),
        ],
      ),
    );
    return found[0] === undefined ? null : toSchedule(this.inputs, found[0]);
  }

  async setAgentVersion(
    orgId: string,
    id: string,
    agentVersion: AgentVersionPin,
    now: number,
  ): Promise<AgentSchedule | null> {
    const found = await this.org(orgId, true, (k) =>
      this.scheduleDocs(
        k,
        `/* pipeshub:harness.schedules_set_version */ UPDATE harness.schedules SET doc = doc || $3::jsonb
          WHERE org_id = $1 AND id = $2 AND doc ? 'agentKey' AND status <> 'disabled' RETURNING doc`,
        [orgId, id, docParam({ agentVersion, updatedAt: now })],
      ),
    );
    return found[0] === undefined ? null : toSchedule(this.inputs, found[0]);
  }

  delete(orgId: string, id: string): Promise<boolean> {
    return this.org(orgId, true, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.schedules_delete */ DELETE FROM harness.schedules WHERE org_id = $1 AND id = $2`,
        [orgId, id],
      );
      if (rowCount !== 1) return false;
      await k.query(
        `/* pipeshub:harness.schedule_firings_delete */ DELETE FROM harness.schedule_firings
          WHERE org_id = $1 AND schedule_id = $2`,
        [orgId, id],
      );
      return true;
    });
  }

  async dueCron(now: number, limit: number): Promise<AgentSchedule[]> {
    const found = await crossOrgUnit(REASON, { write: false }, (k) =>
      this.scheduleDocs(
        k,
        `/* pipeshub:harness.schedules_due */ SELECT doc FROM harness.schedules
          WHERE status = 'active' AND next_fire_at <= $1 ORDER BY next_fire_at, org_id, id LIMIT $2`,
        [now, limit],
      ),
    );
    return this.open(found);
  }

  async claimCronSlot(
    firing: ScheduleFiring,
    expectedNextFireAt: number,
    nextFireAt: number | null,
  ): Promise<ScheduleFiring | null> {
    try {
      return await this.org(firing.orgId, true, async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.schedules_move_cursor */ UPDATE harness.schedules SET doc = doc || $4::jsonb
            WHERE org_id = $1 AND id = $2 AND status = 'active' AND next_fire_at = $3`,
          [
            firing.orgId,
            firing.scheduleId,
            expectedNextFireAt,
            docParam({ nextFireAt, updatedAt: firing.createdAt }),
          ],
        );
        if (rowCount !== 1) return null;
        if (!(await this.insertFiring(k, firing))) throw new SlotTaken();
        return structuredClone(firing);
      });
    } catch (error) {
      if (error instanceof SlotTaken) return null;
      throw error;
    }
  }

  claimSlot(
    firing: ScheduleFiring,
  ): Promise<{ firing: ScheduleFiring; created: boolean }> {
    return this.org(firing.orgId, true, async (k) => {
      if (await this.insertFiring(k, firing))
        return { firing: structuredClone(firing), created: true };
      const existing = await this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_by_slot */ SELECT doc FROM harness.schedule_firings
          WHERE org_id = $1 AND schedule_id = $2 AND slot_key = $3`,
        [firing.orgId, firing.scheduleId, firing.slotKey],
      );
      if (existing[0] === undefined)
        throw new Error('A claimed slot has no firing');
      return { firing: existing[0], created: false };
    });
  }

  async eventSchedules(
    orgId: string,
    kind: EventTriggerKind,
    connectorId?: string,
  ): Promise<AgentSchedule[]> {
    const found = await this.org(orgId, false, (k) =>
      this.scheduleDocs(
        k,
        `/* pipeshub:harness.schedules_by_trigger */ SELECT doc FROM harness.schedules
          WHERE org_id = $1 AND trigger_kind = $2 AND status = 'active'
            AND ($3::boolean IS FALSE OR connector_id IS NOT DISTINCT FROM $4::text)
          ORDER BY created_at, id`,
        [orgId, kind, kind === 'connector', connectorId ?? null],
      ),
    );
    return this.open(found);
  }

  hasActive(kind: EventTriggerKind): Promise<boolean> {
    return crossOrgUnit(REASON, { write: false }, async (k) => {
      const { rows } = await k.query<{ found: boolean }>(
        `/* pipeshub:harness.schedules_has_active */ SELECT EXISTS (
           SELECT 1 FROM harness.schedules WHERE trigger_kind = $1 AND status = 'active') AS found`,
        [kind],
      );
      return rows[0]?.found === true;
    });
  }

  settle(
    orgId: string,
    firingId: string,
    outcome: FiringOutcome,
    now: number,
  ): Promise<boolean> {
    const admitted = outcome.status === 'admitted';
    return this.updateFiring(
      'settle',
      orgId,
      firingId,
      `status = 'pending'`,
      [],
      {
        status: outcome.status,
        runId: admitted ? outcome.runId : null,
        sessionId: admitted ? outcome.sessionId : null,
        reason: admitted ? null : outcome.reason,
        watchUntil: admitted ? outcome.watchUntil : null,
        settledAt: now,
      },
    );
  }

  async release(
    orgId: string,
    firingId: string,
    attempts: number,
    leaseUntil: number,
  ): Promise<void> {
    await this.updateFiring(
      'release',
      orgId,
      firingId,
      `status = 'pending' AND attempts = $4`,
      [attempts],
      {
        leaseUntil,
      },
    );
  }

  takeStale(
    now: number,
    leaseMs: number,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    return crossOrgUnit(REASON, { write: true }, async (k) => {
      // A firing another firer holds the row of is skipped, not waited for; the guard repeats the candidate test on
      // the locked row, so a firing settled or retaken meanwhile is never taken twice.
      const taken = await this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_take_stale */ UPDATE harness.schedule_firings f
            SET doc = f.doc || jsonb_build_object('attempts', f.attempts + 1, 'leaseUntil', $1::bigint + $2::bigint)
          FROM (SELECT org_id, id FROM harness.schedule_firings
                 WHERE status = 'pending' AND lease_until <= $1
                 ORDER BY lease_until, org_id, id LIMIT $3 FOR UPDATE SKIP LOCKED) c
          WHERE f.org_id = c.org_id AND f.id = c.id AND f.status = 'pending' AND f.lease_until <= $1
          RETURNING f.doc`,
        [now, leaseMs, limit],
      );
      return taken.sort((a, b) => a.leaseUntil - b.leaseUntil);
    });
  }

  firings(
    orgId: string,
    scheduleId: string,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    return this.org(orgId, false, (k) =>
      this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_list */ SELECT doc FROM harness.schedule_firings
          WHERE org_id = $1 AND schedule_id = $2 ORDER BY slot_at DESC, id LIMIT $3`,
        [orgId, scheduleId, limit],
      ),
    );
  }

  async getFiring(
    orgId: string,
    firingId: string,
  ): Promise<ScheduleFiring | null> {
    const found = await this.org(orgId, false, (k) =>
      this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_get */ SELECT doc FROM harness.schedule_firings
          WHERE org_id = $1 AND id = $2`,
        [orgId, firingId],
      ),
    );
    return found[0] ?? null;
  }

  async firingBySlot(
    orgId: string,
    scheduleId: string,
    slotKey: string,
  ): Promise<ScheduleFiring | null> {
    const found = await this.org(orgId, false, (k) =>
      this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_by_slot */ SELECT doc FROM harness.schedule_firings
          WHERE org_id = $1 AND schedule_id = $2 AND slot_key = $3`,
        [orgId, scheduleId, slotKey],
      ),
    );
    return found[0] ?? null;
  }

  watched(now: number, limit: number): Promise<ScheduleFiring[]> {
    return crossOrgUnit(REASON, { write: false }, (k) =>
      this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_watched */ SELECT doc FROM harness.schedule_firings
          WHERE status = 'admitted' AND watch_until > $1
          ORDER BY watch_checked_at NULLS FIRST, org_id, id LIMIT $2`,
        [now - WATCH_GRACE_MS, limit],
      ),
    );
  }

  async markChecked(
    orgId: string,
    firingId: string,
    now: number,
  ): Promise<void> {
    await this.updateFiring('checked', orgId, firingId, 'true', [], {
      watchCheckedAt: now,
    });
  }

  markNotified(orgId: string, firingId: string, now: number): Promise<boolean> {
    return this.updateFiring(
      'notified',
      orgId,
      firingId,
      `(doc->>'notifiedAt') IS NULL`,
      [],
      {
        notifiedAt: now,
      },
    );
  }

  unwatch(orgId: string, firingId: string, now: number): Promise<boolean> {
    return this.updateFiring(
      'unwatch',
      orgId,
      firingId,
      'watch_until IS NOT NULL',
      [],
      {
        watchUntil: null,
        watchEndedAt: now,
      },
    );
  }

  activeFirings(
    orgId: string,
    scheduleId: string,
    limit: number,
  ): Promise<ScheduleFiring[]> {
    return this.org(orgId, false, (k) =>
      this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_active */ SELECT doc FROM harness.schedule_firings
          WHERE org_id = $1 AND schedule_id = $2 AND status = 'admitted' AND watch_until IS NOT NULL
          ORDER BY slot_at DESC, id LIMIT $3`,
        [orgId, scheduleId, limit],
      ),
    );
  }

  async queuedFiring(
    orgId: string,
    scheduleId: string,
  ): Promise<ScheduleFiring | null> {
    const found = await this.org(orgId, false, (k) =>
      this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_queued */ SELECT doc FROM harness.schedule_firings
          WHERE org_id = $1 AND schedule_id = $2 AND status = 'pending' AND doc ? 'queuedBehind'
          ORDER BY slot_at, id LIMIT 1`,
        [orgId, scheduleId],
      ),
    );
    return found[0] ?? null;
  }

  requeue(
    orgId: string,
    firingId: string,
    attempts: number,
    runId: string,
    leaseUntil: number,
  ): Promise<boolean> {
    return this.updateFiring(
      'requeue',
      orgId,
      firingId,
      `status = 'pending' AND attempts = $4`,
      [attempts],
      {
        queuedBehind: runId,
        leaseUntil,
        reason: 'overlap_queued',
        attempts: attempts > 1 ? attempts - 1 : attempts,
      },
    );
  }

  fixContext(
    orgId: string,
    firingId: string,
    context: RunTriggerContext,
  ): Promise<RunTriggerContext> {
    return this.org(orgId, true, async (k) => {
      // The first attempt's context wins: a later one finds it set (under the row lock) and reads it back.
      await k.query(
        `/* pipeshub:harness.schedule_firings_fix_context */ UPDATE harness.schedule_firings SET doc = doc || $3::jsonb
          WHERE org_id = $1 AND id = $2 AND NOT doc ? 'context'`,
        [orgId, firingId, docParam({ context })],
      );
      const found = await this.firingDocs(
        k,
        `/* pipeshub:harness.schedule_firings_get */ SELECT doc FROM harness.schedule_firings
          WHERE org_id = $1 AND id = $2`,
        [orgId, firingId],
      );
      const fixed = found[0]?.context;
      if (fixed === undefined)
        throw new Error('A firing has no trigger context');
      return fixed;
    });
  }

  countDelivery(
    orgId: string,
    scheduleId: string,
    windowStart: number,
    expiresAt: number,
  ): Promise<number> {
    return this.org(orgId, true, async (k) => {
      // The windows a TTL index drops on MongoDB: any that expired before this window began has expired by now.
      await k.query(
        `/* pipeshub:harness.schedule_counters_expire */ DELETE FROM harness.schedule_counters
          WHERE org_id = $1 AND expires_at IS NOT NULL AND expires_at < $2`,
        [orgId, windowStart],
      );
      const { rows } = await k.query<{ count: string }>(
        `/* pipeshub:harness.schedule_counters_hit */ INSERT INTO harness.schedule_counters AS c
           (org_id, key, count, updated_at, expires_at, region)
         VALUES ($1, $2, 1, $3, $4, $5)
         ON CONFLICT (org_id, key) DO UPDATE SET count = c.count + 1, updated_at = $3, expires_at = $4
         RETURNING count::text AS count`,
        [
          orgId,
          `hits:${orgId}:${scheduleId}:${String(windowStart)}`,
          windowStart,
          expiresAt,
          rowRegion(),
        ],
      );
      return Number(rows[0]?.count);
    });
  }

  firstDelivery(
    orgId: string,
    scheduleId: string,
    nonce: string,
    at: number,
    expiresAt: number,
  ): Promise<boolean> {
    return this.org(orgId, true, async (k) => {
      const { rows } = await k.query<{ count: string }>(
        `/* pipeshub:harness.schedule_counters_seen */ INSERT INTO harness.schedule_counters AS c
           (org_id, key, count, updated_at, expires_at, region)
         VALUES ($1, $2, 1, $3, $4, $5)
         ON CONFLICT (org_id, key) DO UPDATE SET count = c.count + 1, updated_at = $3
         RETURNING count::text AS count`,
        [
          orgId,
          `seen:${orgId}:${scheduleId}:${nonce}`,
          at,
          expiresAt,
          rowRegion(),
        ],
      );
      return Number(rows[0]?.count) === 1;
    });
  }

  /** A deleted user's schedules go entirely (CLEANUP-G2): an account restored later starts with none. */
  eraseUser(orgId: string, userId: string): Promise<number> {
    return this.remove(orgId, userId);
  }

  eraseOrg(orgId: string): Promise<number> {
    return this.remove(orgId, null);
  }

  /** The schedules, their firings and the counters that counted them; `actorId` null: every one of the org. */
  private remove(orgId: string, actorId: string | null): Promise<number> {
    return this.org(orgId, true, async (k) => {
      const { rows } = await k.query<{ id: string }>(
        `/* pipeshub:harness.schedules_erase */ DELETE FROM harness.schedules
          WHERE org_id = $1 AND ($2::text IS NULL OR actor_id = $2) RETURNING id`,
        [orgId, actorId],
      );
      const ids = rows.map((row) => row.id);
      if (ids.length > 0)
        await k.query(
          `/* pipeshub:harness.schedule_firings_erase */ DELETE FROM harness.schedule_firings
            WHERE org_id = $1 AND schedule_id = ANY($2::text[])`,
          [orgId, ids],
        );
      await k.query(
        `/* pipeshub:harness.schedule_counters_erase */ DELETE FROM harness.schedule_counters
          WHERE org_id = $1 AND ($2::text IS NULL OR key = $2)`,
        [orgId, actorId === null ? null : `actor:${orgId}:${actorId}`],
      );
      return ids.length;
    });
  }

  eraseAgent(orgId: string, agentKey: string): Promise<number> {
    return this.org(orgId, true, async (k) => {
      const now = await this.time.now(k);
      const { rowCount } = await k.query(
        `/* pipeshub:harness.schedules_disable_agent */ UPDATE harness.schedules SET doc = doc || $3::jsonb
          WHERE org_id = $1 AND agent_key = $2`,
        [
          orgId,
          agentKey,
          docParam({
            status: 'disabled',
            statusReason: 'agent_deleted',
            input: null,
            nextFireAt: null,
            webhookTokenHash: null,
            webhookSigningKey: null,
            updatedAt: now,
          }),
        ],
      );
      return rowCount ?? 0;
    });
  }

  pauseUser(orgId: string, userId: string, reason: string): Promise<number> {
    return this.org(orgId, true, async (k) => {
      const now = await this.time.now(k);
      const { rowCount } = await k.query(
        `/* pipeshub:harness.schedules_pause_user */ UPDATE harness.schedules SET doc = doc || $3::jsonb
          WHERE org_id = $1 AND actor_id = $2 AND status = 'active'`,
        [
          orgId,
          userId,
          docParam({
            status: 'paused',
            statusReason: reason,
            nextFireAt: null,
            updatedAt: now,
          }),
        ],
      );
      return rowCount ?? 0;
    });
  }

  private org<T>(
    orgId: string,
    write: boolean,
    fn: (k: PgKernelTx) => Promise<T>,
  ): Promise<T> {
    return orgOrJobsUnit(undefined, orgId, REASON, { write }, fn);
  }

  private async insertFiring(
    k: PgKernelTx,
    firing: ScheduleFiring,
  ): Promise<boolean> {
    const { rowCount } = await k.query(
      `/* pipeshub:harness.schedule_firings_insert */ INSERT INTO harness.schedule_firings (org_id, id, doc, region)
       VALUES ($1, $2, $3::jsonb, $4)
       ON CONFLICT DO NOTHING`,
      [firing.orgId, firing.id, docParam(firingDocument(firing)), rowRegion()],
    );
    return rowCount === 1;
  }

  /** Sets `patch` on a firing where `guard` (on `$4`… for `extra`) holds; whether it did. `step` names the statement. */
  private updateFiring(
    step: 'settle' | 'release' | 'checked' | 'notified' | 'unwatch' | 'requeue',
    orgId: string,
    firingId: string,
    guard: string,
    extra: unknown[],
    patch: Record<string, unknown>,
  ): Promise<boolean> {
    return this.org(orgId, true, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.schedule_firings_${step} */ UPDATE harness.schedule_firings SET doc = doc || $3::jsonb
          WHERE org_id = $1 AND id = $2 AND ${guard}`,
        [orgId, firingId, docParam(patch), ...extra],
      );
      return rowCount === 1;
    });
  }

  private async scheduleDocs(
    k: PgKernelTx,
    text: string,
    values: unknown[],
  ): Promise<ScheduleDocument[]> {
    const { rows } = await k.query<{ doc: unknown }>(text, values);
    return rows.map((row) => decodeDoc<ScheduleDocument>(row.doc));
  }

  private async firingDocs(
    k: PgKernelTx,
    text: string,
    values: unknown[],
  ): Promise<ScheduleFiring[]> {
    const { rows } = await k.query<{ doc: unknown }>(text, values);
    return rows.map((row) => firingOf(row.doc));
  }

  private open(documents: ScheduleDocument[]): Promise<AgentSchedule[]> {
    return Promise.all(documents.map((d) => toSchedule(this.inputs, d)));
  }
}
