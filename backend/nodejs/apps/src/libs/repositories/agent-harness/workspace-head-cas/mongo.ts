import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession } from 'mongoose';
import { AgentWorkspaceHeadModel } from '../execution/schema/execution.schema';

export interface WorkspaceHeadMove {
  orgId: string;
  workspaceId: string;
  expectedRevision: number;
  revision: number;
  runId: string;
  /** Recorded only when this move creates the head (expectedRevision 0). */
  onCreate?: {
    forkOf: { workspaceId: string; revision: number };
    mergeBase: number;
  };
}

/**
 * The one CAS on `agentWorkspaceHeads`, shared by the journal commit's
 * `workspaceHeadCAS` and the workspace store: from 0 it inserts (the unique
 * (orgId, workspaceId) index makes a concurrent creator lose), otherwise it
 * moves only from `expectedRevision`. false when the head was elsewhere.
 */
export async function moveWorkspaceHead(
  move: WorkspaceHeadMove,
  now: number,
  sessionHandle: Tx | ClientSession,
): Promise<boolean> {
  const session = asMongoSession(sessionHandle);
  if (move.expectedRevision === 0) {
    const prior = await AgentWorkspaceHeadModel.findOneAndUpdate(
      { orgId: move.orgId, workspaceId: move.workspaceId },
      {
        $setOnInsert: {
          revision: move.revision,
          runId: move.runId,
          updatedAt: now,
          ...(move.onCreate ?? {}),
        },
      },
      { upsert: true, new: false, session },
    ).lean();
    return prior === null;
  }
  const result = await AgentWorkspaceHeadModel.updateOne(
    {
      orgId: move.orgId,
      workspaceId: move.workspaceId,
      revision: move.expectedRevision,
    },
    { $set: { revision: move.revision, runId: move.runId, updatedAt: now } },
    { session },
  );
  return result.modifiedCount === 1;
}
