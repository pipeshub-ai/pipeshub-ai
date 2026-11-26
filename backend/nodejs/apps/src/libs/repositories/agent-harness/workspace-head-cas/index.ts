import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import {
  moveWorkspaceHead as moveOnMongo,
  type WorkspaceHeadMove,
} from './mongo';
import { moveWorkspaceHead as moveOnPg } from './pg';

export type { WorkspaceHeadMove };

/** The workspace head CAS of the selected document store, run in the caller's transaction. */
export interface WorkspaceHeadCasStore {
  move(move: WorkspaceHeadMove, now: number, tx: Tx): Promise<boolean>;
}

export const createWorkspaceHeadCas = (): WorkspaceHeadCasStore =>
  pickHarnessStore<WorkspaceHeadCasStore>('agent-harness/workspace-head-cas', {
    mongo: () => ({
      move: moveOnMongo,
    }),
    postgres: () => ({ move: moveOnPg }),
  });
