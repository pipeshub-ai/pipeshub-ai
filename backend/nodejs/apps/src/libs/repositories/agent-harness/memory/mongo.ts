import { idOperator } from '../../id-filter-guard/mongo';
import type { QueryFilter } from 'mongoose';
import { MemoryEntry } from '../../../../modules/agent-harness/domain/memory';
import {
  MemoryChange,
  MemoryEraser,
  MemoryFilter,
  MemoryStore,
  PendingRun,
} from '../../../../modules/agent-harness/ports/memory-store';
import { AgentMemoryModel, MemoryDocument } from './schema/memory.schema';

const documentId = (orgId: string, memoryId: string): string =>
  `${orgId}:${memoryId}`;

const DUPLICATE_KEY = 11000;

const entryOf = (document: MemoryDocument): MemoryEntry => {
  const { _id: _ignored, ...entry } = document;
  return structuredClone<MemoryEntry>(entry);
};

/** `MemoryStore` over `agentMemories` (indexes: migrations `memories` and `memory-settlement`). */
export class MongoMemoryStore implements MemoryStore, MemoryEraser {
  async propose(
    entry: MemoryEntry,
  ): Promise<{ memory: MemoryEntry; created: boolean }> {
    const _id = documentId(entry.orgId, entry.memoryId);
    try {
      await AgentMemoryModel.create([{ ...entry, _id }]);
      return { memory: structuredClone(entry), created: true };
    } catch (error) {
      if ((error as { code?: unknown }).code !== DUPLICATE_KEY) throw error;
    }
    const stored = await AgentMemoryModel.findById(_id).lean();
    if (stored === null) {
      throw new Error('memory vanished between insert and read');
    }
    return { memory: entryOf(stored), created: false };
  }

  async get(orgId: string, memoryId: string): Promise<MemoryEntry | null> {
    const stored = await AgentMemoryModel.findById(
      documentId(orgId, memoryId),
    ).lean();
    return stored === null ? null : entryOf(stored);
  }

  async byIds(
    orgId: string,
    memoryIds: readonly string[],
  ): Promise<MemoryEntry[]> {
    if (memoryIds.length === 0) return [];
    const stored = await AgentMemoryModel.find({
      _id: { $in: [...new Set(memoryIds)].map((id) => documentId(orgId, id)) },
    }).lean();
    return stored.map((document) => entryOf(document as MemoryDocument));
  }

  async list(orgId: string, filter: MemoryFilter): Promise<MemoryEntry[]> {
    const query: QueryFilter<MemoryDocument> = { orgId };
    if (filter.ownerId !== undefined) query.ownerId = filter.ownerId;
    if (filter.agentKey !== undefined) query.agentKey = filter.agentKey;
    if (filter.runId !== undefined) query.runId = filter.runId;
    if (filter.scopes !== undefined) query.scope = { $in: [...filter.scopes] };
    if (filter.statuses !== undefined) {
      query.status = { $in: [...filter.statuses] };
    }
    if (filter.after !== undefined) {
      query.$or = [
        { createdAt: { $lt: filter.after.createdAt } },
        {
          createdAt: filter.after.createdAt,
          memoryId: idOperator({ $gt: filter.after.memoryId }),
        },
      ];
    }
    const stored = await AgentMemoryModel.find(query)
      .sort({ createdAt: -1, memoryId: 1 })
      .limit(filter.limit)
      .lean();
    return stored.map((document) => entryOf(document as MemoryDocument));
  }

  async update(
    orgId: string,
    memoryId: string,
    change: MemoryChange,
    expected?: MemoryEntry['status'],
  ): Promise<MemoryEntry | null> {
    const set = Object.fromEntries(
      Object.entries(change).filter(([, value]) => value !== undefined),
    );
    const updated = await AgentMemoryModel.findOneAndUpdate(
      {
        _id: documentId(orgId, memoryId),
        ...(expected !== undefined ? { status: expected } : {}),
      },
      { $set: set },
      { returnDocument: 'after' },
    ).lean();
    return updated === null ? null : entryOf(updated);
  }

  async delete(orgId: string, memoryId: string): Promise<boolean> {
    const result = await AgentMemoryModel.deleteOne({
      _id: documentId(orgId, memoryId),
    });
    if (result.deletedCount !== 1) return false;
    await this.eraseSuperseded(orgId, [memoryId]);
    return true;
  }

  async eraseSession(orgId: string, sessionId: string): Promise<number> {
    return this.eraseWithHistory(orgId, { orgId, sessionId });
  }

  async pendingRuns(before: number, limit: number): Promise<PendingRun[]> {
    const runs = await AgentMemoryModel.aggregate<{
      _id: PendingRun;
    }>([
      {
        $match: {
          status: 'pending',
          createdAt: { $lt: before },
          runId: idOperator({ $ne: null }),
        },
      },
      {
        $group: {
          _id: { orgId: '$orgId', runId: '$runId' },
          first: { $min: '$createdAt' },
        },
      },
      { $sort: { first: 1, '_id.orgId': 1, '_id.runId': 1 } },
      { $limit: Math.max(limit, 1) },
    ]);
    return runs.map(({ _id }) => ({ orgId: _id.orgId, runId: _id.runId }));
  }

  async eraseUser(orgId: string, userId: string): Promise<number> {
    return this.eraseWithHistory(orgId, { orgId, ownerId: userId });
  }

  async eraseOrg(orgId: string): Promise<number> {
    return (await AgentMemoryModel.deleteMany({ orgId })).deletedCount;
  }

  /** Deletes what `query` matches and, transitively, what those memories superseded (FIX-R2 C2). */
  private async eraseWithHistory(
    orgId: string,
    query: QueryFilter<MemoryDocument>,
  ): Promise<number> {
    const ids = (
      await AgentMemoryModel.find(query, { memoryId: 1 }).lean()
    ).map((document) => (document as MemoryDocument).memoryId);
    if (ids.length === 0) return 0;
    const erased = (await AgentMemoryModel.deleteMany(query)).deletedCount;
    return erased + (await this.eraseSuperseded(orgId, ids));
  }

  private async eraseSuperseded(
    orgId: string,
    memoryIds: readonly string[],
  ): Promise<number> {
    let count = 0;
    let gone = [...memoryIds];
    while (gone.length > 0) {
      const query = { orgId, supersededBy: { $in: gone } };
      gone = (await AgentMemoryModel.find(query, { memoryId: 1 }).lean()).map(
        (document) => (document as MemoryDocument).memoryId,
      );
      if (gone.length === 0) break;
      count += (await AgentMemoryModel.deleteMany(query)).deletedCount;
    }
    return count;
  }

  async eraseAgent(orgId: string, agentKey: string): Promise<number> {
    return (
      await AgentMemoryModel.deleteMany({ orgId, scope: 'agent', agentKey })
    ).deletedCount;
  }
}
