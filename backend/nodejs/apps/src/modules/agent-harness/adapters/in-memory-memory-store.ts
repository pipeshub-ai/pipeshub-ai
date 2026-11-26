/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import { MemoryEntry, newestFirst } from '../domain/memory';
import {
  MemoryChange,
  MemoryEraser,
  MemoryFilter,
  MemoryStore,
  PendingRun,
} from '../ports/memory-store';

const key = (orgId: string, memoryId: string): string =>
  `${orgId}\u0000${memoryId}`;

const copy = (entry: MemoryEntry): MemoryEntry => structuredClone(entry);

/** The reference `MemoryStore` (tests and single-process use). */
export class InMemoryMemoryStore implements MemoryStore, MemoryEraser {
  private readonly entries = new Map<string, MemoryEntry>();

  async propose(
    entry: MemoryEntry,
  ): Promise<{ memory: MemoryEntry; created: boolean }> {
    const stored = this.entries.get(key(entry.orgId, entry.memoryId));
    if (stored !== undefined) return { memory: copy(stored), created: false };
    this.entries.set(key(entry.orgId, entry.memoryId), copy(entry));
    return { memory: copy(entry), created: true };
  }

  async get(orgId: string, memoryId: string): Promise<MemoryEntry | null> {
    const stored = this.entries.get(key(orgId, memoryId));
    return stored === undefined ? null : copy(stored);
  }

  async byIds(
    orgId: string,
    memoryIds: readonly string[],
  ): Promise<MemoryEntry[]> {
    return [...new Set(memoryIds)].flatMap((memoryId) => {
      const stored = this.entries.get(key(orgId, memoryId));
      return stored === undefined ? [] : [copy(stored)];
    });
  }

  async list(orgId: string, filter: MemoryFilter): Promise<MemoryEntry[]> {
    return [...this.entries.values()]
      .filter(
        (entry) =>
          entry.orgId === orgId &&
          (filter.ownerId === undefined || entry.ownerId === filter.ownerId) &&
          (filter.agentKey === undefined ||
            entry.agentKey === filter.agentKey) &&
          (filter.runId === undefined || entry.runId === filter.runId) &&
          (filter.scopes === undefined ||
            filter.scopes.includes(entry.scope)) &&
          (filter.statuses === undefined ||
            filter.statuses.includes(entry.status)) &&
          (filter.after === undefined ||
            entry.createdAt < filter.after.createdAt ||
            (entry.createdAt === filter.after.createdAt &&
              entry.memoryId > filter.after.memoryId)),
      )
      .sort(newestFirst)
      .slice(0, filter.limit)
      .map(copy);
  }

  async update(
    orgId: string,
    memoryId: string,
    change: MemoryChange,
    expected?: MemoryEntry['status'],
  ): Promise<MemoryEntry | null> {
    const stored = this.entries.get(key(orgId, memoryId));
    if (stored === undefined) return null;
    if (expected !== undefined && stored.status !== expected) return null;
    const updated: MemoryEntry = {
      ...stored,
      ...Object.fromEntries(
        Object.entries(change).filter(([, value]) => value !== undefined),
      ),
    };
    this.entries.set(key(orgId, memoryId), copy(updated));
    return copy(updated);
  }

  async delete(orgId: string, memoryId: string): Promise<boolean> {
    if (!this.entries.delete(key(orgId, memoryId))) return false;
    this.eraseSuperseded(orgId, [memoryId]);
    return true;
  }

  async eraseSession(orgId: string, sessionId: string): Promise<number> {
    return this.erase(
      (entry) => entry.orgId === orgId && entry.sessionId === sessionId,
    );
  }

  async pendingRuns(before: number, limit: number): Promise<PendingRun[]> {
    const first = new Map<string, { run: PendingRun; createdAt: number }>();
    for (const entry of this.entries.values()) {
      if (
        entry.status !== 'pending' ||
        entry.runId === null ||
        entry.createdAt >= before
      ) {
        continue;
      }
      const runKey = key(entry.orgId, entry.runId);
      const seen = first.get(runKey);
      if (seen === undefined || entry.createdAt < seen.createdAt) {
        first.set(runKey, {
          run: { orgId: entry.orgId, runId: entry.runId },
          createdAt: entry.createdAt,
        });
      }
    }
    return [...first.values()]
      .sort((a, b) =>
        a.createdAt !== b.createdAt
          ? a.createdAt - b.createdAt
          : `${a.run.orgId}\u0000${a.run.runId}`.localeCompare(
              `${b.run.orgId}\u0000${b.run.runId}`,
            ),
      )
      .slice(0, limit)
      .map(({ run }) => run);
  }

  async eraseUser(orgId: string, userId: string): Promise<number> {
    return this.erase(
      (entry) => entry.orgId === orgId && entry.ownerId === userId,
    );
  }

  async eraseOrg(orgId: string): Promise<number> {
    return this.erase((entry) => entry.orgId === orgId);
  }

  async eraseAgent(orgId: string, agentKey: string): Promise<number> {
    return this.erase(
      (entry) =>
        entry.orgId === orgId &&
        entry.scope === 'agent' &&
        entry.agentKey === agentKey,
    );
  }

  private erase(matches: (entry: MemoryEntry) => boolean): number {
    const erased = new Map<string, string[]>();
    for (const [id, entry] of this.entries) {
      if (matches(entry)) {
        this.entries.delete(id);
        erased.set(entry.orgId, [
          ...(erased.get(entry.orgId) ?? []),
          entry.memoryId,
        ]);
      }
    }
    let count = 0;
    for (const [orgId, ids] of erased) {
      count += ids.length + this.eraseSuperseded(orgId, ids);
    }
    return count;
  }

  /** The memories `memoryIds` superseded, transitively (FIX-R2 C2); answers how many went. */
  private eraseSuperseded(orgId: string, memoryIds: readonly string[]): number {
    let count = 0;
    let gone = new Set(memoryIds);
    while (gone.size > 0) {
      const next = new Set<string>();
      for (const [id, entry] of this.entries) {
        if (entry.orgId === orgId && gone.has(entry.supersededBy ?? '')) {
          this.entries.delete(id);
          next.add(entry.memoryId);
        }
      }
      count += next.size;
      gone = next;
    }
    return count;
  }
}
