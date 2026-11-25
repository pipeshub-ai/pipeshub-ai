import type { CommitFence } from './commit-fence';

/**
 * Who besides a run's actor may decide its approvals (P2.6c, ARCHITECTURE
 * 4.9): anyone a bound directory answers is an approver approves or denies;
 * org admins may only deny. Read from the org's records, never from the request.
 */
export interface ApproverDirectory {
  isApprover(orgId: string, userId: string): Promise<boolean>;
  isOrgAdmin(orgId: string, userId: string): Promise<boolean>;
  /**
   * QA21-F2: the user's approver standing, re-read and held inside the
   * decision's transaction, so the approver's removal and their decision
   * cannot both commit. `refuse` is what the decision fails with when the
   * standing is gone. Absent: the standing read before the transaction stands.
   */
  approverFence?(
    orgId: string,
    userId: string,
    refuse: () => Error,
  ): CommitFence;
  /**
   * The refusals' words where this directory names more approvers than the run's actor. Absent: the usual
   * rule's words (only the person the run acts for approves).
   */
  readonly refusalWords?: Readonly<
    Partial<Record<'APPROVER_REQUIRED' | 'NOT_ALLOWED_TO_DECIDE', string>>
  >;
}

/** No approvers, no admins: only the run's actor (and the session owner, to deny). */
export const actorOnlyApprovers: ApproverDirectory = {
  isApprover: () => Promise.resolve(false),
  isOrgAdmin: () => Promise.resolve(false),
};
