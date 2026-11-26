import {
  MergeStatus,
  WorkspaceError,
} from '../../../../modules/agent-harness/domain/workspace';
import {
  ManifestSummary,
  MergeRecord,
  StoredManifest,
  WorkspaceAppend,
  WorkspaceHeadRecord,
  WorkspaceKeyRecord,
  WorkspaceKeyStore,
  WorkspaceRepository,
  WorkspaceWriterStore,
  WriterAcquired,
  WriterLeaseRecord,
} from '../../../../modules/agent-harness/ports/workspace-store';
import {
  inMongoTransaction,
  MAJORITY_WRITE,
} from '../durable-transaction/mongo';
import { AgentWorkspaceHeadModel } from '../execution/schema/execution.schema';
import { moveWorkspaceHead } from '../workspace-head-cas/mongo';
import {
  AgentWorkspaceKeyModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
  WorkspaceManifestDocument,
} from './schema/workspace.schema';

const isDuplicateKey = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === 11000;

const notAFork = (): WorkspaceError =>
  new WorkspaceError('WORKSPACE_NOT_A_FORK', 'Not a fork');

const stored = (document: WorkspaceManifestDocument): StoredManifest => ({
  orgId: document.orgId,
  workspaceId: document.workspaceId,
  revision: document.revision,
  parentRevision: document.parentRevision ?? null,
  contentHash: document.contentHash,
  fileCount: document.fileCount,
  files: {
    v: document.files.v,
    kid: document.files.kid,
    kv: document.files.kv,
    iv: document.files.iv,
    tag: document.files.tag,
    ct: document.files.ct,
  },
  reason: document.reason,
  runId: document.runId,
  createdAt: document.createdAt,
});

/** Heads in `agentWorkspaceHeads`, revisions in `agentWorkspaceManifests`, one transaction per append. */
export class MongoWorkspaceRepository implements WorkspaceRepository {
  async head(
    orgId: string,
    workspaceId: string,
  ): Promise<WorkspaceHeadRecord | null> {
    const document = await AgentWorkspaceHeadModel.findOne({
      orgId,
      workspaceId,
    })
      .read('primary')
      .lean();
    if (document === null) return null;
    return {
      orgId,
      workspaceId,
      revision: document.revision,
      forkOf: document.forkOf
        ? {
            workspaceId: document.forkOf.workspaceId,
            revision: document.forkOf.revision,
          }
        : null,
      mergeBase: document.mergeBase ?? null,
      lastMerge: document.lastMerge
        ? {
            key: document.lastMerge.key,
            into: document.lastMerge.into,
            base: document.lastMerge.base,
            theirs: document.lastMerge.theirs,
            ours: document.lastMerge.ours,
            revision: document.lastMerge.revision ?? null,
            status: document.lastMerge.status as MergeStatus,
          }
        : null,
    };
  }

  async manifest(
    orgId: string,
    workspaceId: string,
    revision: number,
  ): Promise<StoredManifest | null> {
    const document = await AgentWorkspaceManifestModel.findOne({
      orgId,
      workspaceId,
      revision,
    })
      .read('primary')
      .lean();
    return document === null ? null : stored(document);
  }

  async revisions(
    orgId: string,
    workspaceId: string,
    page: { limit: number; before?: number },
  ): Promise<ManifestSummary[]> {
    const documents = await AgentWorkspaceManifestModel.find(
      {
        orgId,
        workspaceId,
        ...(page.before !== undefined
          ? { revision: { $lt: page.before } }
          : {}),
      },
      { files: 0 },
    )
      .sort({ revision: -1 })
      .limit(page.limit)
      .read('primary')
      .lean();
    return documents.map((document) => ({
      orgId: document.orgId,
      workspaceId: document.workspaceId,
      revision: document.revision,
      parentRevision: document.parentRevision ?? null,
      contentHash: document.contentHash,
      fileCount: document.fileCount,
      reason: document.reason,
      runId: document.runId,
      createdAt: document.createdAt,
    }));
  }

  async append(write: WorkspaceAppend): Promise<void> {
    const { manifest } = write;
    await inMongoTransaction(async (session) => {
      const moved = await moveWorkspaceHead(
        {
          orgId: manifest.orgId,
          workspaceId: manifest.workspaceId,
          expectedRevision: write.expectedRevision,
          revision: manifest.revision,
          runId: manifest.runId,
          onCreate:
            write.forkOf === undefined
              ? undefined
              : { forkOf: write.forkOf, mergeBase: manifest.revision },
        },
        manifest.createdAt,
        session,
      );
      if (!moved) {
        throw new WorkspaceError(
          'WORKSPACE_CONFLICT',
          'Workspace HEAD moved since the expected revision',
        );
      }
      try {
        await AgentWorkspaceManifestModel.create([manifest], { session });
      } catch (error) {
        // A revision already stored under this head: someone else's write won.
        if (isDuplicateKey(error)) {
          throw new WorkspaceError(
            'WORKSPACE_CONFLICT',
            'Workspace revision already exists',
          );
        }
        throw error;
      }
      if (write.mergedFork !== undefined) {
        const result = await AgentWorkspaceHeadModel.updateOne(
          {
            orgId: manifest.orgId,
            workspaceId: write.mergedFork.workspaceId,
            forkOf: { $ne: null },
          },
          {
            $set: mergedForkFields(
              write.mergedFork.mergeBase,
              write.mergedFork.record,
            ),
          },
          { session },
        );
        if (result.matchedCount !== 1) throw notAFork();
      }
    });
  }

  async setMergeBase(
    orgId: string,
    forkId: string,
    mergeBase: number,
    record?: MergeRecord,
  ): Promise<void> {
    const result = await AgentWorkspaceHeadModel.updateOne(
      { orgId, workspaceId: forkId, forkOf: { $ne: null } },
      { $set: mergedForkFields(mergeBase, record) },
      MAJORITY_WRITE,
    );
    if (result.matchedCount !== 1) throw notAFork();
  }
}

const mergedForkFields = (
  mergeBase: number,
  record: MergeRecord | undefined,
): Record<string, unknown> =>
  record === undefined ? { mergeBase } : { mergeBase, lastMerge: record };

/**
 * Write leases in `agentWorkspaceWriters`, one document per workspace (unique
 * org, workspace). A free or expired lease, or the holder's own, is taken by
 * one atomic update; an acquirer that finds another live holder loses the
 * upsert on the unique index and is told who holds it.
 */
export class MongoWorkspaceWriterStore implements WorkspaceWriterStore {
  async acquire(
    lease: WriterLeaseRecord,
    now: number,
    takeOverFrom?: string,
  ): Promise<WriterAcquired> {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const answer = await this.acquireOnce(lease, now, takeOverFrom);
      // null: the holder released between the losing upsert and its read.
      if (answer !== null) return answer;
    }
    throw new Error('Workspace writer lease kept changing hands');
  }

  private async acquireOnce(
    lease: WriterLeaseRecord,
    now: number,
    takeOverFrom: string | undefined,
  ): Promise<WriterAcquired | null> {
    try {
      const prior = await AgentWorkspaceWriterModel.findOneAndUpdate(
        {
          orgId: lease.orgId,
          workspaceId: lease.workspaceId,
          $or: [
            { runId: lease.runId },
            { expiresAt: { $lte: now } },
            ...(takeOverFrom !== undefined ? [{ runId: takeOverFrom }] : []),
          ],
        },
        {
          $set: {
            sessionId: lease.sessionId,
            runId: lease.runId,
            expiresAt: lease.expiresAt,
            updatedAt: now,
          },
        },
        { upsert: true, new: false, ...MAJORITY_WRITE },
      ).lean();
      const renewed =
        prior !== null && prior.runId === lease.runId && prior.expiresAt > now;
      return { acquired: true, lease: { ...lease }, renewed };
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
      const holder = await AgentWorkspaceWriterModel.findOne({
        orgId: lease.orgId,
        workspaceId: lease.workspaceId,
      })
        .read('primary')
        .lean();
      if (holder === null || holder.runId === lease.runId) return null;
      return { acquired: false, holder: holder.runId };
    }
  }

  async release(
    orgId: string,
    workspaceId: string,
    runId: string,
  ): Promise<boolean> {
    const result = await AgentWorkspaceWriterModel.deleteOne(
      { orgId, workspaceId, runId },
      MAJORITY_WRITE,
    );
    return result.deletedCount === 1;
  }
}

/** Session keys (D33); majority writes so an issued key or a shred survives failover. */
export class MongoWorkspaceKeyStore implements WorkspaceKeyStore {
  async get(
    orgId: string,
    sessionId: string,
  ): Promise<WorkspaceKeyRecord | null> {
    const document = await AgentWorkspaceKeyModel.findOne({ orgId, sessionId })
      .read('primary')
      .readConcern('majority')
      .lean();
    if (document === null) return null;
    return {
      orgId,
      sessionId,
      keyId: document.keyId,
      wrapped: document.wrapped
        ? {
            v: document.wrapped.v,
            kid: document.wrapped.kid,
            kv: document.wrapped.kv,
            iv: document.wrapped.iv,
            tag: document.wrapped.tag,
            ct: document.wrapped.ct,
          }
        : null,
      createdAt: document.createdAt,
      destroyedAt: document.destroyedAt ?? null,
    };
  }

  async insert(record: WorkspaceKeyRecord): Promise<boolean> {
    try {
      await new AgentWorkspaceKeyModel(record).save({ w: 'majority' });
      return true;
    } catch (error) {
      if (isDuplicateKey(error)) return false;
      throw error;
    }
  }

  async destroy(
    orgId: string,
    sessionId: string,
    at: number,
  ): Promise<boolean> {
    const prior = await AgentWorkspaceKeyModel.findOneAndUpdate(
      { orgId, sessionId },
      [
        {
          $set: {
            wrapped: null,
            keyId: { $ifNull: ['$keyId', ''] },
            createdAt: { $ifNull: ['$createdAt', at] },
            destroyedAt: { $ifNull: ['$destroyedAt', at] },
          },
        },
      ],
      { updatePipeline: true, upsert: true, new: false, ...MAJORITY_WRITE },
    ).lean();
    return prior?.wrapped != null;
  }
}
