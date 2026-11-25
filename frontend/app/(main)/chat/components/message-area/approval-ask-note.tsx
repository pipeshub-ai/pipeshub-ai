import type { ApprovalReason, DecisionAuthority, PendingApproval } from '../../types';

export interface ApprovalAskNoteProps {
  reason: ApprovalReason | undefined;
  /** The viewer may approve the ask. */
  canApprove: boolean;
  authority?: DecisionAuthority;
  /** Approvals counted toward an ask that needs several (counts only). */
  approvals?: PendingApproval['approvals'];
  /** The viewer's own approval of the ask is counted. */
  approvedByYou?: boolean;
}

/**
 * Who an ask a rule decides waits on, under the card's buttons. Editions plug in via config.ts; this default
 * renders nothing (no ask carries such a rule).
 */
export function ApprovalAskNote(_props: ApprovalAskNoteProps): null {
  return null;
}
