import type {
  AgentErasureQueue,
  PendingAgentErasure,
} from '../../../../modules/agent-harness/ports/agent-erasures';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import {
  decodeDoc,
  docParam,
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';
import type { AgentErasureDocument } from './document';

type StoredEntry = Omit<AgentErasureDocument, '_id'>;

const REASON = 'harness agent erasure';

const toEntry = (doc: StoredEntry): PendingAgentErasure => ({
  orgId: doc.orgId,
  agentKey: doc.agentKey,
  confirmed: doc.confirmed ?? true,
  failures: doc.failures,
  requestedAt: doc.requestedAt,
  nextAttemptAt: doc.nextAttemptAt,
  lastErrorType: doc.lastErrorType ?? null,
});

/**
 * Pending agent erasures on PostgreSQL (`harness.agent_erasures`), due on database time. `intend` and `enqueue` decide
 * from the entry as it is: a fresh one is the insert's own row, an existing one is read under its row lock, so two
 * deletes of one agent racing each other apply one after the other.
 */
export class PgAgentErasureQueue implements AgentErasureQueue {
  constructor(private readonly time: PgTime = pgServerTime) {}

  async intend(
    orgId: string,
    agentKey: string,
    afterMs: number,
  ): Promise<void> {
    await this.upsert(
      orgId,
      agentKey,
      (now) => ({
        orgId,
        agentKey,
        confirmed: false,
        failures: 0,
        requestedAt: now,
        nextAttemptAt: now + afterMs,
        lastErrorType: null,
      }),
      (old, now) =>
        old.confirmed === false
          ? { ...old, requestedAt: now, nextAttemptAt: now + afterMs }
          : null,
    );
  }

  async enqueue(
    orgId: string,
    agentKey: string,
    errorType: string,
  ): Promise<void> {
    await this.upsert(
      orgId,
      agentKey,
      (now) => ({
        orgId,
        agentKey,
        confirmed: true,
        failures: 1,
        requestedAt: now,
        nextAttemptAt: now,
        lastErrorType: errorType,
      }),
      (old, now) =>
        old.confirmed === false
          ? {
              ...old,
              confirmed: true,
              failures: old.failures + 1,
              nextAttemptAt: now,
              lastErrorType: errorType,
            }
          : null,
    );
  }

  due(now: number, limit: number): Promise<PendingAgentErasure[]> {
    return jobsUnit(REASON, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.agent_erasures_due */ SELECT doc FROM harness.agent_erasures
          WHERE next_attempt_at_ms <= $1 ORDER BY next_attempt_at_ms, org_id, agent_key LIMIT $2`,
        [now, limit],
      );
      return rows.map((row) => toEntry(decodeDoc<StoredEntry>(row.doc)));
    });
  }

  async retryLater(
    entry: PendingAgentErasure,
    at: number,
    errorType: string,
  ): Promise<void> {
    await this.update(
      entry.orgId,
      `/* pipeshub:harness.agent_erasures_retry */ UPDATE harness.agent_erasures
          SET doc = doc || jsonb_build_object('nextAttemptAt', $3::bigint, 'lastErrorType', $4::text,
            'failures', (doc->>'failures')::int + 1)
        WHERE org_id = $1 AND agent_key = $2`,
      [entry.orgId, entry.agentKey, at, errorType],
    );
  }

  async postpone(entry: PendingAgentErasure, at: number): Promise<void> {
    await this.update(
      entry.orgId,
      `/* pipeshub:harness.agent_erasures_postpone */ UPDATE harness.agent_erasures
          SET doc = doc || jsonb_build_object('nextAttemptAt', $3::bigint)
        WHERE org_id = $1 AND agent_key = $2`,
      [entry.orgId, entry.agentKey, at],
    );
  }

  async settle(orgId: string, agentKey: string): Promise<void> {
    await this.update(
      orgId,
      `/* pipeshub:harness.agent_erasures_settle */ DELETE FROM harness.agent_erasures
        WHERE org_id = $1 AND agent_key = $2`,
      [orgId, agentKey],
    );
  }

  async withdraw(entry: PendingAgentErasure): Promise<boolean> {
    const removed = await this.update(
      entry.orgId,
      `/* pipeshub:harness.agent_erasures_withdraw */ DELETE FROM harness.agent_erasures
        WHERE org_id = $1 AND agent_key = $2
          AND (doc->>'confirmed')::boolean IS FALSE AND (doc->>'requestedAt')::bigint = $3`,
      [entry.orgId, entry.agentKey, entry.requestedAt],
    );
    return removed === 1;
  }

  private update(
    orgId: string,
    text: string,
    values: unknown[],
  ): Promise<number> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => (await k.query(text, values)).rowCount ?? 0,
    );
  }

  /** The fresh entry, inserted; else `change` of the entry under its row lock (null: left as it is). */
  private upsert(
    orgId: string,
    agentKey: string,
    fresh: (now: number) => StoredEntry,
    change: (old: StoredEntry, now: number) => StoredEntry | null,
  ): Promise<void> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        const now = await this.time.now(k);
        for (;;) {
          if (await this.insert(k, fresh(now))) return;
          const { rows } = await k.query<{ doc: unknown }>(
            `/* pipeshub:harness.agent_erasures_lock */ SELECT doc FROM harness.agent_erasures
              WHERE org_id = $1 AND agent_key = $2 FOR UPDATE`,
            [orgId, agentKey],
          );
          // Settled between the insert and the lock: insert again.
          if (rows[0] === undefined) continue;
          const next = change(decodeDoc<StoredEntry>(rows[0].doc), now);
          if (next === null) return;
          await k.query(
            `/* pipeshub:harness.agent_erasures_write */ UPDATE harness.agent_erasures SET doc = $3::jsonb
              WHERE org_id = $1 AND agent_key = $2`,
            [orgId, agentKey, docParam(next)],
          );
          return;
        }
      },
    );
  }

  private async insert(k: PgKernelTx, entry: StoredEntry): Promise<boolean> {
    const { rows } = await k.query(
      `/* pipeshub:harness.agent_erasures_insert */ INSERT INTO harness.agent_erasures (org_id, agent_key, doc, region)
         VALUES ($1, $2, $3::jsonb, $4) ON CONFLICT (org_id, agent_key) DO NOTHING RETURNING 1 AS created`,
      [entry.orgId, entry.agentKey, docParam(entry), rowRegion()],
    );
    return rows.length === 1;
  }
}
