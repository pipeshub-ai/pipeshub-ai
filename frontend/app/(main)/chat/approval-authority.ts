import { approvalAskRules } from '@/config';
import type { ApprovalReason, DecisionAuthority, PendingApproval } from './types';

/**
 * Whether the viewer may approve this call. An ask a rule decides goes by that rule, and Approve stays hidden until
 * the authority is known: the person who asked must never be offered it. `perCall`: the authority is this call's own
 * (the approvals queue), not the run's.
 */
export function mayApprove(
  authority: DecisionAuthority | undefined,
  reason: ApprovalReason | undefined,
  perCall = false,
  interactionId?: string,
): boolean {
  if (!approvalAskRules.holds(reason)) return authority ? authority.approve : true;
  if (!authority) return false;
  if (perCall) return authority.approve;
  return (
    approvalAskRules.mayApprove(authority, { reason, ...(interactionId !== undefined ? { interactionId } : {}) }) ??
    authority.approve
  );
}

/** The viewer may approve this card: by `mayApprove`, and not once their own approval of it is counted. */
export function mayApproveCard(
  authority: DecisionAuthority | undefined,
  approval: Pick<PendingApproval, 'reason' | 'approvedByYou' | 'interactionId'>,
  perCall = false,
): boolean {
  if (approval.approvedByYou === true) return false;
  return mayApprove(authority, approval.reason, perCall, approval.interactionId);
}
