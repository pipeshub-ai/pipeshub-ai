import type {
  ApprovalDecision,
  ApprovalRequest,
  ApprovalTarget,
} from '../domain/objects';
import type { ApprovalTargetHandler } from './approval-targets';
import type { AuthzActor } from './authorizer';
import type { Applied } from './transaction';

export interface SubmitApproval {
  readonly actor: AuthzActor;
  readonly target: Pick<ApprovalTarget, 'type' | 'id'>;
  readonly change: ApprovalRequest['change'];
  /**
   * The requester's own verdict, given with the request (a facade where an
   * approver both asks and decides). Refused unless they are an approver.
   */
  readonly decision?: Pick<ApprovalDecision, 'decision' | 'subset' | 'note'>;
}

export interface DecideApproval {
  readonly actor: AuthzActor;
  readonly id: string;
  readonly decision: Pick<ApprovalDecision, 'decision' | 'subset' | 'note'>;
  /** The target revision the approver looked at (02-lld §9.1 `targetRevision`). */
  readonly targetRevision?: number;
}

export interface ApprovalOutcome {
  readonly request: ApprovalRequest;
  /** What the applied change's effect answered (a facade forwards it). */
  readonly applied: Applied<unknown> | null;
}

/**
 * Where a configuration change waits for its approvers (02-lld §2.6): the
 * request is recorded, and applied through the target's handler once the
 * people its policy names decide it.
 */
export interface ConfigChangeApprovals {
  submit(command: SubmitApproval): Promise<ApprovalOutcome>;
}

/**
 * The extension point the MCP manifest and skill candidate routes read
 * (`CONTROL_PLANE_APPROVALS_FACTORY`): bound, a change through them is an
 * approval request over the given target handlers; unbound, the admin's own
 * request applies it at once.
 */
export type ConfigChangeApprovalsFactory = (
  targets: readonly ApprovalTargetHandler[],
) => ConfigChangeApprovals;
