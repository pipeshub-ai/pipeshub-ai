import { MemoryEntry } from '../../../../modules/agent-harness/domain/memory';
import {
  MemoryChange,
  MemoryEraser,
  MemoryFilter,
  MemoryStore,
  PendingRun,
} from '../../../../modules/agent-harness/ports/memory-store';
import { Params } from '../../pg-util';
import { crossOrgUnit } from '../pg-documents/ambient';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import {
  decodeDoc,
  docParam,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';

const REASON = 'memories';

const entryOf = (doc: unknown): MemoryEntry => decodeDoc<MemoryEntry>(doc);

/**
 * `MemoryStore` over `harness.memories` (P7.3): a proposal's id is its idempotency key (`ON CONFLICT DO NOTHING`, then
 * the stored one), a guarded update moves a status only from the one expected, and an erasure takes, in its own unit,
 * the memories it matched and everything they superseded (FIX-R2 C2).
 */
export class PgMemoryStore implements MemoryStore, MemoryEraser {
  propose(
    entry: MemoryEntry,
  ): Promise<{ memory: MemoryEntry; created: boolean }> {
    return orgOrJobsUnit(
      undefined,
      entry.orgId,
      REASON,
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.memories_propose */ INSERT INTO harness.memories (org_id, memory_id, doc, region)
         VALUES ($1, $2, $3::jsonb, $4)
         ON CONFLICT (org_id, memory_id) DO NOTHING`,
          [entry.orgId, entry.memoryId, docParam(entry), rowRegion()],
        );
        if (rowCount === 1)
          return { memory: structuredClone(entry), created: true };
        const stored = await this.one(k, entry.orgId, entry.memoryId);
        if (stored === null)
          throw new Error('memory vanished between insert and read');
        return { memory: stored, created: false };
      },
    );
  }

  get(orgId: string, memoryId: string): Promise<MemoryEntry | null> {
    return orgOrJobsUnit(undefined, orgId, REASON, { write: false }, (k) =>
      this.one(k, orgId, memoryId),
    );
  }

  async byIds(
    orgId: string,
    memoryIds: readonly string[],
  ): Promise<MemoryEntry[]> {
    if (memoryIds.length === 0) return [];
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.memories_by_ids */ SELECT doc FROM harness.memories
          WHERE org_id = $1 AND memory_id = ANY($2::text[]) ORDER BY memory_id`,
          [orgId, [...new Set(memoryIds)]],
        );
        return rows.map((row) => entryOf(row.doc));
      },
    );
  }

  list(orgId: string, filter: MemoryFilter): Promise<MemoryEntry[]> {
    const p = new Params();
    const where = [`org_id = ${p.add(orgId)}`];
    if (filter.ownerId !== undefined)
      where.push(`owner_id = ${p.add(filter.ownerId)}`);
    if (filter.agentKey !== undefined)
      where.push(`agent_key = ${p.add(filter.agentKey)}`);
    if (filter.runId !== undefined)
      where.push(`run_id = ${p.add(filter.runId)}`);
    if (filter.scopes !== undefined)
      where.push(`scope = ANY(${p.add([...filter.scopes])}::text[])`);
    if (filter.statuses !== undefined)
      where.push(`status = ANY(${p.add([...filter.statuses])}::text[])`);
    if (filter.after !== undefined) {
      const at = p.add(filter.after.createdAt);
      where.push(
        `(created_at < ${at} OR (created_at = ${at} AND memory_id > ${p.add(filter.after.memoryId)}))`,
      );
    }
    const limit = p.add(filter.limit);
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.memories_list */ SELECT doc FROM harness.memories WHERE ${where.join(' AND ')}
          ORDER BY created_at DESC, memory_id LIMIT ${limit}`,
          p.values,
        );
        return rows.map((row) => entryOf(row.doc));
      },
    );
  }

  update(
    orgId: string,
    memoryId: string,
    change: MemoryChange,
    expected?: MemoryEntry['status'],
  ): Promise<MemoryEntry | null> {
    const set = Object.fromEntries(
      Object.entries(change).filter(([, value]) => value !== undefined),
    );
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.memories_update */ UPDATE harness.memories SET doc = doc || $3::jsonb
          WHERE org_id = $1 AND memory_id = $2 AND ($4::text IS NULL OR status = $4)
          RETURNING doc`,
          [orgId, memoryId, docParam(set), expected ?? null],
        );
        return rows[0] === undefined ? null : entryOf(rows[0].doc);
      },
    );
  }

  delete(orgId: string, memoryId: string): Promise<boolean> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        const gone = await this.erase(k, orgId, 'memory_id = $2', memoryId);
        if (gone.length !== 1) return false;
        await this.eraseSuperseded(k, orgId, gone);
        return true;
      },
    );
  }

  eraseSession(orgId: string, sessionId: string): Promise<number> {
    return this.eraseWithHistory(orgId, 'session_id = $2', sessionId);
  }

  eraseUser(orgId: string, userId: string): Promise<number> {
    return this.eraseWithHistory(orgId, 'owner_id = $2', userId);
  }

  pendingRuns(before: number, limit: number): Promise<PendingRun[]> {
    return crossOrgUnit(REASON, { write: false }, async (k) => {
      const { rows } = await k.query<{ org_id: string; run_id: string }>(
        `/* pipeshub:harness.memories_pending_runs */ SELECT org_id, run_id FROM harness.memories
          WHERE status = 'pending' AND created_at < $1 AND run_id IS NOT NULL
          GROUP BY org_id, run_id ORDER BY min(created_at), org_id, run_id LIMIT $2`,
        [before, Math.max(limit, 1)],
      );
      return rows.map((row) => ({ orgId: row.org_id, runId: row.run_id }));
    });
  }

  eraseOrg(orgId: string): Promise<number> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.memories_erase_org */ DELETE FROM harness.memories WHERE org_id = $1`,
          [orgId],
        );
        return rowCount ?? 0;
      },
    );
  }

  eraseAgent(orgId: string, agentKey: string): Promise<number> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.memories_erase_agent */ DELETE FROM harness.memories
          WHERE org_id = $1 AND scope = 'agent' AND agent_key = $2`,
          [orgId, agentKey],
        );
        return rowCount ?? 0;
      },
    );
  }

  private eraseWithHistory(
    orgId: string,
    predicate: string,
    value: string,
  ): Promise<number> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        const gone = await this.erase(k, orgId, predicate, value);
        if (gone.length === 0) return 0;
        return gone.length + (await this.eraseSuperseded(k, orgId, gone));
      },
    );
  }

  /** Deletes the org's memories `predicate` (on `$2`) matches; their ids. */
  private async erase(
    k: PgKernelTx,
    orgId: string,
    predicate: string,
    value: string,
  ): Promise<string[]> {
    const { rows } = await k.query<{ memory_id: string }>(
      `/* pipeshub:harness.memories_erase */ DELETE FROM harness.memories WHERE org_id = $1 AND ${predicate}
        RETURNING memory_id`,
      [orgId, value],
    );
    return rows.map((row) => row.memory_id);
  }

  private async eraseSuperseded(
    k: PgKernelTx,
    orgId: string,
    memoryIds: readonly string[],
  ): Promise<number> {
    let count = 0;
    let gone = [...memoryIds];
    while (gone.length > 0) {
      const { rows } = await k.query<{ memory_id: string }>(
        `/* pipeshub:harness.memories_erase_superseded */ DELETE FROM harness.memories
          WHERE org_id = $1 AND superseded_by = ANY($2::text[]) RETURNING memory_id`,
        [orgId, gone],
      );
      gone = rows.map((row) => row.memory_id);
      count += gone.length;
    }
    return count;
  }

  private async one(
    k: PgKernelTx,
    orgId: string,
    memoryId: string,
  ): Promise<MemoryEntry | null> {
    const { rows } = await k.query<{ doc: unknown }>(
      `/* pipeshub:harness.memories_get */ SELECT doc FROM harness.memories WHERE org_id = $1 AND memory_id = $2`,
      [orgId, memoryId],
    );
    return rows[0] === undefined ? null : entryOf(rows[0].doc);
  }
}
