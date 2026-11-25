import type {
  EffectiveApprovals,
  FlowRule,
  OrgApprovalRules,
} from '../../domain/approvals';
import { todayApprovals } from '../../domain/deployment-policy';
import type { ConfigDocumentStore } from '../../ports/config-document-store';

/**
 * What the worker decides per call today for a run of (org, agent), read from
 * the approvals document as the worker reads it: the shadow comparison's side
 * of the pinned `approvals`.
 */
export const workerApprovals =
  (
    approvals: ConfigDocumentStore<unknown>,
    flow: FlowRule | null,
    edition: OrgApprovalRules | null = null,
  ) =>
  async (orgId: string, agentKey: string | null): Promise<EffectiveApprovals> =>
    todayApprovals(
      (await approvals.read()).value,
      orgId,
      agentKey,
      flow,
      edition,
    );
