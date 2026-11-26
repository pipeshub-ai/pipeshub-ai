/**
 * A user's or org's personal toolset instances and MCP servers (main #4324),
 * which the connectors service keeps in the config store with their
 * sign-ins, tool rules and owner pointers (C-14 PRINCIPAL_DATA_STORES
 * `personalInstances`). Deleting the user or the org erases them first; a
 * hook that throws leaves the principal undeleted, so a retry reaches them.
 */
export interface PersonalInstanceEraser {
  /** How many personal toolset instances of the user went. */
  eraseUser(orgId: string, userId: string): Promise<number>;
  /** How many personal toolset instances of the org's members went. */
  eraseOrg(orgId: string): Promise<number>;
}

export const PERSONAL_INSTANCE_ERASER = 'AgentHarnessPersonalInstanceEraser';
