import type { AgentDetail } from '../types';

/** Accept camelCase or snake_case from the Node API. */
type AgentDetailFlags = AgentDetail & {
  is_service_account?: boolean;
  canEdit?: boolean;
};

function readIsServiceAccount(agent: AgentDetail): boolean {
  const a = agent as AgentDetailFlags;
  return a.isServiceAccount === true || a.is_service_account === true;
}

/** User may not persist agent changes (explicit false from API). */
function readDeniedEdit(agent: AgentDetail): boolean {
  const a = agent as AgentDetailFlags;
  return a.can_edit === false || a.canEdit === false;
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
