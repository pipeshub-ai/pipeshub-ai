import type { Tx } from '../../uow';
import {
  orgUnit,
  docParam,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import type { WorkspaceHeadMove } from './mongo';

/**
 * The one CAS on `harness.workspace_heads`, shared by the journal commit's `workspaceHeadCAS` and the workspace store:
 * from 0 it inserts (the primary key makes a concurrent creator insert nothing), otherwise it moves only from
 * `expectedRevision`, a guard in the UPDATE: of two movers from one revision the second, once the first commits,
 * finds the head elsewhere. false when the head was elsewhere.
 */
export async function moveWorkspaceHeadIn(
  k: PgKernelTx,
  move: WorkspaceHeadMove,
  now: number,
): Promise<boolean> {
  if (move.expectedRevision === 0) {
    const { rowCount } = await k.query(
      `/* pipeshub:harness.workspace_heads_create */ INSERT INTO harness.workspace_heads (org_id, workspace_id, doc, region)
       VALUES ($1, $2, $3::jsonb, $4)
       ON CONFLICT (org_id, workspace_id) DO NOTHING`,
      [
        move.orgId,
        move.workspaceId,
        docParam({
          orgId: move.orgId,
          workspaceId: move.workspaceId,
          revision: move.revision,
          runId: move.runId,
          updatedAt: now,
          ...(move.onCreate ?? {}),
        }),
        rowRegion(),
      ],
    );
    return rowCount === 1;
  }
  const { rowCount } = await k.query(
    `/* pipeshub:harness.workspace_heads_move */ UPDATE harness.workspace_heads
        SET doc = doc || jsonb_build_object('revision', $4::bigint, 'runId', $5::text, 'updatedAt', $6::bigint)
      WHERE org_id = $1 AND workspace_id = $2 AND revision = $3`,
    [
      move.orgId,
      move.workspaceId,
      move.expectedRevision,
      move.revision,
      move.runId,
      now,
    ],
  );
  return rowCount === 1;
}

/** `moveWorkspaceHeadIn` in the caller's unit `tx`, which must run as the move's organisation. */
export function moveWorkspaceHead(
  move: WorkspaceHeadMove,
  now: number,
  tx: Tx,
): Promise<boolean> {
  return orgUnit(tx, move.orgId, { write: true }, (k) =>
    moveWorkspaceHeadIn(k, move, now),
  );
}
