import type { AgentDetail } from '../types';

/** Covers both camelCase (typed) and snake_case (legacy API shape) for isServiceAccount. */
type AgentDetailFlags = AgentDetail & {
  is_service_account?: boolean;
};

function readIsServiceAccount(agent: AgentDetail): boolean {
  const a = agent as AgentDetailFlags;
  return a.isServiceAccount === true || a.is_service_account === true;
}

function readDeniedEdit(agent: AgentDetail): boolean {
  return agent.can_edit === false;
}

/**
 * UI capability flags for the agent builder, derived from the loaded agent (if any).
 *
 * Rules:
 * - New agent (no record yet): full edit and persist.
 * - Existing agent with edit denied: no API persist (save / convert).
 * - **isAgentStructureLocked**: lock canvas, flow, and palette items other than per-user toolset auth
 *   (models, KB, apps). Applies to any viewer without `can_edit`.
 * - **isServiceAccountToolsetOrgLocked**: additionally lock org-level service-account toolset
 *   credential UI (sidebar key). Only when service account + edit denied.
 * - Non–service-account viewers may still open user toolset configure / OAuth flows in the Tools section.
 */
export type AgentBuilderPermissions = {
  canPersist: boolean;
  isAgentStructureLocked: boolean;
  isServiceAccountToolsetOrgLocked: boolean;
  isServiceAccount: boolean;
};

export function getAgentBuilderPermissions(loadedAgent: AgentDetail | null): AgentBuilderPermissions {
  if (!loadedAgent) {
    return {
      canPersist: true,
      isAgentStructureLocked: false,
      isServiceAccountToolsetOrgLocked: false,
      isServiceAccount: false,
    };
  }

  const isServiceAccount = readIsServiceAccount(loadedAgent);
  const deniedEdit = readDeniedEdit(loadedAgent);
  const isAgentStructureLocked = deniedEdit;
  const isServiceAccountToolsetOrgLocked = deniedEdit && isServiceAccount;

  return {
    canPersist: !deniedEdit,
    isAgentStructureLocked,
    isServiceAccountToolsetOrgLocked,
    isServiceAccount,
  };
}

/**
 * Whether the viewer may turn this agent into a service agent: its owner or an
 * organization administrator. A service agent searches with someone else's
 * access, so an editor may not decide that; the server refuses it too. A new
 * agent belongs to whoever is creating it; an existing one that has not loaded
 * yet is not offered, so an editor never sees the control flash. An unknown
 * admin state reads as no.
 */
export function canConvertToServiceAgent(
  loadedAgent: AgentDetail | null,
  isAdmin: boolean | null,
  editingExisting: boolean
): boolean {
  if (!loadedAgent) return !editingExisting;
  return loadedAgent.user_role === 'OWNER' || isAdmin === true;
}

/**
 * Whether the org-sharing switch is locked. Turning sharing on needs the
 * agent's can_share as well as the viewer's permission, and the server refuses
 * the whole save otherwise. An agent that is already shared stays switchable,
 * so an editor can turn it off and back on before saving.
 */
export function isOrgShareLocked(loadedAgent: AgentDetail | null, canShareAgent: boolean): boolean {
  return !canShareAgent || Boolean(loadedAgent && !loadedAgent.can_share && !loadedAgent.shareWithOrg);
}
