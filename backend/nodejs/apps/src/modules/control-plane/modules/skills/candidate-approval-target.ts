import { SKILL_CANDIDATE_PREFIX } from '../../domain/approval-targets';
import type { ApprovalRequest, ChangeKind } from '../../domain/objects';
import type {
  ApprovalTargetHandler,
  ApprovalTargetState,
  ApprovalVerdict,
} from '../../ports/approval-targets';
import type { Applied, CpTx } from '../../ports/transaction';

/** The skill store's own write for a decided candidate (today's Python routes, as a driver). */
export interface CandidateEffects {
  approve(candidateId: string, request: ApprovalRequest): Promise<unknown>;
  reject(candidateId: string, request: ApprovalRequest): Promise<unknown>;
}

const PUBLISH: ReadonlySet<ChangeKind> = new Set<ChangeKind>(['publish']);

/**
 * A learned skill candidate as an approval target (02-lld §11.3): approving
 * publishes it as an org skill, so the org's `publish:skill` rule decides who
 * approves (default: an org admin, R2 F3); rejecting discards it.
 */
export class SkillCandidateApprovalTarget implements ApprovalTargetHandler {
  readonly type = 'catalog-item' as const;
  readonly changes = PUBLISH;

  constructor(private readonly effects: CandidateEffects) {}

  current(_orgId: string, id: string): Promise<ApprovalTargetState | null> {
    return Promise.resolve(
      id.startsWith(SKILL_CANDIDATE_PREFIX)
        ? { revision: 0, owner: 'org', kind: 'skill' }
        : null,
    );
  }

  apply(
    tx: CpTx,
    request: ApprovalRequest,
    verdict: ApprovalVerdict,
  ): Promise<Applied<unknown>> {
    const candidateId = request.target.id.slice(SKILL_CANDIDATE_PREFIX.length);
    return Promise.resolve(
      tx.external(() =>
        verdict.state === 'approved'
          ? this.effects.approve(candidateId, request)
          : this.effects.reject(candidateId, request),
      ),
    );
  }
}
