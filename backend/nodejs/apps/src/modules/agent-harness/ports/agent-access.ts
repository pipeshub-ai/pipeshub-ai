import { AgentRunDefinition } from '../domain/agent-definition';

export interface AgentUseRequest {
  orgId: string;
  actorId: string;
  agentKey: string;
  /** The actor's own bearer credential, forwarded to the agent authority. */
  authorization: string;
  /**
   * The published version a schedule pinned (OWD-14); absent: the latest. A
   * version that does not exist is a denial, as no access is.
   */
  agentVersion?: number;
  /**
   * ADR-OWD-07 D-LAZY-2: a live worker lists each MCP server at the run's first use (`mcp_first_use`), so the
   * authority lists none and may grant the default chat's servers whole. Absent: the definition as an older worker
   * needs it.
   */
  mcpFirstUse?: boolean;
}

/**
 * Whether the actor may run the agent, and what it then grants. Execute
 * scope is enough: running an agent must not need `agent:read`. Resolves
 * null when access is denied; rejects when the decision cannot be made, so
 * admission fails closed.
 */
export interface AgentAccessPolicy {
  authorizeRun(request: AgentUseRequest): Promise<AgentRunDefinition | null>;
}
