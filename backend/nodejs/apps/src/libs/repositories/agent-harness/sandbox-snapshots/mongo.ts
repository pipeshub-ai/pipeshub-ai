import type { ClientSession } from 'mongoose';
import { asMongoSession, MAJORITY_WRITE } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import type {
  SandboxSnapshotIndex,
  StoredSandboxSnapshot,
} from '../../../../modules/agent-harness/ports/sandbox-snapshots';
import {
  AgentSandboxSnapshotModel,
  type SandboxSnapshotDocument,
} from './schema/sandbox-snapshots.schema';

const row = (doc: SandboxSnapshotDocument): StoredSandboxSnapshot => ({
  orgId: doc.orgId,
  sessionId: doc.sessionId,
  runId: doc.runId,
  objectId: doc.objectId,
  takenAt: doc.takenAt.getTime(),
  region: doc.region,
});

export class MongoSandboxSnapshotIndex implements SandboxSnapshotIndex<Tx> {
  async record(
    snapshots: readonly StoredSandboxSnapshot[],
    txHandle?: Tx | ClientSession,
  ): Promise<void> {
    const tx = asMongoSession(txHandle);
    if (snapshots.length === 0) return;
    await AgentSandboxSnapshotModel.bulkWrite(
      snapshots.map((snapshot) => ({
        updateOne: {
          filter: { _id: `${snapshot.orgId}:${snapshot.objectId}` },
          update: {
            $setOnInsert: {
              orgId: snapshot.orgId,
              sessionId: snapshot.sessionId,
              runId: snapshot.runId,
              objectId: snapshot.objectId,
              region: snapshot.region,
            },
            $max: { takenAt: new Date(snapshot.takenAt) },
          },
          upsert: true,
        },
      })),
      tx !== undefined ? { session: tx, ordered: false } : { ordered: false },
    );
  }

  async orgs(): Promise<string[]> {
    return (await AgentSandboxSnapshotModel.distinct('orgId')).map(String);
  }

  async takenBefore(
    orgId: string,
    before: number,
    limit: number,
  ): Promise<StoredSandboxSnapshot[]> {
    const docs = await AgentSandboxSnapshotModel.find({
      orgId,
      takenAt: { $lt: new Date(before) },
    })
      .sort({ takenAt: 1 })
      .limit(limit)
      .read('primary')
      .lean();
    return docs.map(row);
  }

  async forget(
    orgId: string,
    objectIds: readonly string[],
    before: number,
  ): Promise<number> {
    if (objectIds.length === 0) return 0;
    const deleted = await AgentSandboxSnapshotModel.deleteMany(
      {
        _id: { $in: objectIds.map((id) => `${orgId}:${id}`) },
        takenAt: { $lt: new Date(before) },
      },
      MAJORITY_WRITE,
    );
    return deleted.deletedCount;
  }

  async forgetSession(orgId: string, sessionId: string): Promise<number> {
    const deleted = await AgentSandboxSnapshotModel.deleteMany(
      { orgId, sessionId },
      MAJORITY_WRITE,
    );
    return deleted.deletedCount;
  }
}
