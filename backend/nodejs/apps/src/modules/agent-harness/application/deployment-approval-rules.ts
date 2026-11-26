import type { ApprovalRules } from '../contracts/generated/core/approval-rules';
import { isApprovalRules, readApprovalsDocument } from '../domain/approvals';
import type { ConfigDocumentStore } from '../ports/config-document-store';

/** The approval rules a chat's sandbox reports its code by (`AGENT_HARNESS_APPROVAL_SETTINGS`). */
export interface ApprovalRulesReader {
  read(orgId: string): Promise<{
    policy: {
      org: ApprovalRules | null;
      agents: Record<string, ApprovalRules>;
    };
    deployment: { default: ApprovalRules | null };
  }>;
}

/**
 * The operator's deployment-wide rule (the approvals document's `default`) and no org's own: this build gives an
 * org no rules of its own, so a run asks by the built-in rules under the deployment's.
 */
export class DeploymentApprovalRules implements ApprovalRulesReader {
  constructor(private readonly store: ConfigDocumentStore<unknown>) {}

  async read(_orgId?: string): ReturnType<ApprovalRulesReader['read']> {
    const read = readApprovalsDocument((await this.store.read()).value);
    const rule = read.status === 'valid' ? read.sections.default : undefined;
    return {
      policy: { org: null, agents: {} },
      deployment: {
        default: rule !== undefined && isApprovalRules(rule) ? rule : null,
      },
    };
  }
}
