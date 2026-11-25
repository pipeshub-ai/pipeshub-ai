import type { Heads, ResolvedRunConfig } from '../../control-plane';
import type {
  AttemptIdentity,
  HarnessRun,
  RunInputSnapshot,
} from '../domain/execution';
import type { PreparedJournalAppend } from '../domain/journal';
import type { PinnedRunSpec, RunVia } from '../domain/run-spec';
import type { ChildBudgetAccount } from './budget-ledger';

/**
 * One child admission (P6.1), everything derived before the transaction:
 * the child's pinned spec and the events of both streams. The store checks
 * the parent's attempt and bounds, then writes all of it or nothing.
 */
export interface AdmitChildCommand {
  orgId: string;
  parentRunId: string;
  attempt: AttemptIdentity;
  childRunId: string;
  invocationId: string;
  /** Same invocation, different request: an idempotency conflict. */
  requestHash: string;
  /** The parent's pinned `budgets.maxChildren`. */
  maxChildren: number;
  input: RunInputSnapshot;
  pinned: PinnedRunSpec;
  via: RunVia;
  /** `run.admitted` and `run.spec_pinned` on the child's own stream. */
  childJournal: PreparedJournalAppend;
  /** `child.spawned` on the parent's stream. */
  parentJournal: PreparedJournalAppend;
  /**
   * The child's budget account (P6.5), opened in the same transaction by a
   * store that holds a ledger; a share the parent cannot cover refuses the
   * whole admission (`BUDGET_SHARE_EXCEEDS_REMAINING`).
   */
  budget?: ChildBudgetAccount;
  /** A sub-agent role set the child's model: `child.spawned` names it, which only `child_models` workers fold (MA1). */
  roleModel?: boolean;
  /** The child's pin under its parent's (CPD-5 `on`); kept on the child's run. Absent: its parent has none. */
  resolved?: ResolvedRunConfig;
  /** The revisions the child's pin was resolved at: the store admits it only while both stand (`PinStale`). */
  resolvedAt?: Heads;
}

export interface ChildAdmission {
  run: HarnessRun;
  /** False: this invocation's child was admitted before; nothing was written. */
  created: boolean;
}

export interface ChildRunStore {
  /** Fenced on the parent's attempt; idempotent on the child's run id. */
  admitChild(command: AdmitChildCommand): Promise<ChildAdmission>;
  /** The run's children that have not ended. */
  openChildren(orgId: string, parentRunId: string): Promise<HarnessRun[]>;
  /** Children that have not ended although their parent has (oldest first, bounded). */
  orphanedChildren(limit: number): Promise<HarnessRun[]>;
}
