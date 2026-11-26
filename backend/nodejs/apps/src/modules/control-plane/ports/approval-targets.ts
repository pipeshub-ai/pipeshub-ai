import type {
  ApprovalRequest,
  ApprovalTarget,
  ChangeKind,
} from '../domain/objects';
import type { Applied, CpTx } from './transaction';

/** What the workflow needs of a target before it asks anyone: where it is, who owns it, what it is. */
export interface ApprovalTargetState {
  readonly revision: number;
  /** `org`, `team:<id>` or `user:<id>`. */
  readonly owner: string;
  /** The catalog item kind the approval rules key on (`mcp`, `skill`, ...). */
  readonly kind: string;
}

/** What a decision applies: an approval (of all the change, or the subset of its tools named) or a rejection. */
export type ApprovalVerdict =
  | { readonly state: 'approved'; readonly subset: readonly string[] | null }
  | { readonly state: 'rejected' };

/**
 * One target type's side of approvals (02-lld §6.2): reads the target and
 * applies a decided change to it by compare-and-set on the request's target
 * revision. A handler whose target moved throws `ControlPlaneRevisionConflictError`
 * (inside the transaction) or `ApprovalTargetMoved` (from an external effect).
 */
export interface ApprovalTargetHandler {
  readonly type: ApprovalTarget['type'];
  /** The changes this handler can apply. */
  readonly changes: ReadonlySet<ChangeKind>;
  /** `null` when the target does not exist in the org. */
  current(orgId: string, id: string): Promise<ApprovalTargetState | null>;
  /** What the change's effect answered, when it has one the caller shows (a facade's response). */
  apply(
    tx: CpTx,
    request: ApprovalRequest,
    verdict: ApprovalVerdict,
  ): Promise<Applied<unknown> | null>;
}
