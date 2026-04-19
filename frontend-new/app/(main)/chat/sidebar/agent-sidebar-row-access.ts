import type { AgentListRecord } from '@/app/(main)/agents/types';

/**
 * Minimal fields the access helpers read — satisfied by both
 * `AgentListRecord` (list payload) and `AgentDetail` (single-agent payload),
 * so callers don't need to cast between the two.
 */
export type AgentAccessInput = Pick<
  AgentListRecord,
  | 'id'
  | '_key'
  | 'can_edit'
  | 'can_delete'
  | 'can_view'
  | 'isServiceAccount'
  | 'shareWithOrg'
  | 'user_role'
>;

function isNonOwnerRole(userRole: string | undefined): boolean {
  const normalized = String(userRole ?? '').trim().toUpperCase();
  return normalized.length > 0 && normalized !== 'OWNER';
}

/**
 * Non–service-account agent that is org-visible or assigned to the user as a non-owner
 * (shared / collaborator access).
 */
export function isSharedIndividualAgent(agent: AgentAccessInput): boolean {
  if (agent.isServiceAccount === true) return false;
  if (agent.shareWithOrg === true) return true;
  return isNonOwnerRole(agent.user_role);
}

export type ViewAgentTooltipVariant = 'individual' | 'service_account';

export type AgentSidebarRowMenuAccess = {
  agentKey: string;
  canEdit: boolean;
  canDelete: boolean;
  /**
   * Offer “View agent” in the row menu when the user may open the builder but cannot edit:
   * shared non–service-account agents, or service-account agents (builder is structure-locked without edit).
   */
  showViewAgent: boolean;
  /** When {@link showViewAgent}, which tooltip explains view-only vs locked service agent. */
  viewAgentTooltipVariant?: ViewAgentTooltipVariant;
  showMenu: boolean;
};

/**
 * Derives meatball-menu visibility for a list row. Callers should still handle a missing key.
 */
export function getAgentSidebarRowMenuAccess(agent: AgentAccessInput): AgentSidebarRowMenuAccess | null {
  const agentKey = agent.id || agent._key;
  if (!agentKey) return null;

  const canEdit = Boolean(agent.can_edit);
  const canDelete = Boolean(agent.can_delete);
  const canOpenBuilder = agent.can_view !== false;
  const isServiceAccount = agent.isServiceAccount === true;
  const viewEligible =
    isServiceAccount || isSharedIndividualAgent(agent);
  const showViewAgent = viewEligible && canOpenBuilder && !canEdit;
  const viewAgentTooltipVariant: ViewAgentTooltipVariant | undefined = showViewAgent
    ? isServiceAccount
      ? 'service_account'
      : 'individual'
    : undefined;

  return {
    agentKey,
    canEdit,
    canDelete,
    showViewAgent,
    viewAgentTooltipVariant,
    showMenu: canEdit || canDelete || showViewAgent,
  };
}
