import type { ModelOverride, StreamChatRequest } from '../types';

/** Total models answering one question: the primary plus MAX_COMPARE_MODELS - 1 branches. */
export const MAX_COMPARE_MODELS = 2;

/** The request the branch re-sends: the primary's, minus its conversation and compare flag. */
export type BranchRequest = Omit<StreamChatRequest, 'conversationId' | 'projectId' | 'compareTurn' | 'runId'>;

export interface CompareTurn {
  /** Distinguishes this turn from a later one on the same slot, for async callbacks. */
  turnId: number;
  /** [primary, branch]; null for a turn restored after reload, where only the saved answers' labels are known. */
  models: ModelOverride[] | null;
  displayQuery: string;
  template: BranchRequest | null;
  /** Null until the primary stream's `turn_started`. */
  userMessageId: string | null;
  parentConvId: string | null;
  branchConvId: string | null;
  branchSlotId: string | null;
  /** `starting`: branch not created yet; `ready`: the branch slot exists; `failed`: the branch could not be created. */
  phase: 'starting' | 'ready' | 'failed';
  choosing: boolean;
  chooseError: 'conflict' | 'failed' | null;
}

export type ColumnStatus = 'starting' | 'streaming' | 'waiting' | 'error' | 'empty' | 'done';
