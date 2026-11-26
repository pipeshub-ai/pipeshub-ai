import { eq } from '../../id-filter-guard/mongo';
import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession } from 'mongoose';
import { AgentWorkspaceHeadModel } from '../execution/schema/execution.schema';
import { checkedWorkspaceHeadMove, type WorkspaceHeadMove } from './move';

export type { WorkspaceHeadMove };

/**
 * The one CAS on `agentWorkspaceHeads`, shared by the journal commit's
 * `workspaceHeadCAS` and the workspace store: from 0 it inserts (the unique
 * (orgId, workspaceId) index makes a concurrent creator lose), otherwise it
 * moves only from `expectedRevision`. false when the head was elsewhere.
 */
export async function moveWorkspaceHead(
  given: WorkspaceHeadMove,
  now: number,
  sessionHandle: Tx | ClientSession,
): Promise<boolean> {
  const move = checkedWorkspaceHeadMove(given);
  const updatedAt = now;
  const session = asMongoSession(sessionHandle);
  if (move.expectedRevision === 0) {
    const prior = await AgentWorkspaceHeadModel.findOneAndUpdate(
      { orgId: eq(move.orgId), workspaceId: eq(move.workspaceId) },
      {
        $setOnInsert: move.onCreate
          ? {
              revision: move.revision,
              runId: move.runId,
              updatedAt,
              forkOf: {
                workspaceId: move.onCreate.forkOf.workspaceId,
                revision: move.onCreate.forkOf.revision,
              },
              mergeBase: move.onCreate.mergeBase,
            }
          : { revision: move.revision, runId: move.runId, updatedAt },
      },
      { upsert: true, new: false, session },
    ).lean();
    return prior === null;
  }
  const result = await AgentWorkspaceHeadModel.updateOne(
    {
      orgId: eq(move.orgId),
      workspaceId: eq(move.workspaceId),
      revision: eq(move.expectedRevision),
    },
    { $set: { revision: move.revision, runId: move.runId, updatedAt } },
    { session },
  );
  return result.modifiedCount === 1;
}
