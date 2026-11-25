import {
  skillInstallationId,
  type EffectiveSetShadow,
  type LegacyPin,
} from '../../control-plane';
import type { AgentRunDefinition } from '../domain/agent-definition';
import { HarnessUnavailableError } from '../domain/errors';
import { ASSISTANT_AGENT_DEFINITION_KEY } from '../domain/rollout';
import type { AgentAccessPolicy, AgentUseRequest } from '../ports/agent-access';

/** What today's agent authority grants a run, as the control plane's shadow compares it. */
export function legacyPinOf(definition: AgentRunDefinition): LegacyPin {
  return {
    toolsetTools: definition.toolsets.flatMap((toolset) =>
      toolset.instanceId === null
        ? []
        : toolset.tools.map(
            (tool) => `${toolset.name}@${toolset.instanceId}/${tool.name}`,
          ),
    ),
    mcpTools: definition.mcpServers.flatMap((server) =>
      server.tools.map((tool) => `mcp@${server.instanceId}/${tool.name}`),
    ),
    skills: definition.skills.map((skill) => skill.name),
  };
}

/** The definition without the items the actor may not see: toolsets and MCP servers by instance, skills by name. */
export function withoutHidden(
  definition: AgentRunDefinition,
  hidden: ReadonlySet<string>,
): AgentRunDefinition {
  return {
    ...definition,
    toolsets: definition.toolsets.filter(
      (toolset) =>
        toolset.instanceId === null || !hidden.has(toolset.instanceId),
    ),
    mcpServers: definition.mcpServers.filter(
      (server) => !hidden.has(server.instanceId),
    ),
    skills: definition.skills.filter(
      (skill) => !hidden.has(skillInstallationId(skill.name)),
    ),
  };
}

/**
 * The definition with only what a migrated org's effective set holds ready
 * (CPD-10 R3): a toolset or MCP server by instance, narrowed to the tools the
 * set holds, and a skill by its installation. A toolset without an instance
 * is the platform's own, not a control-plane item, and stays; so do knowledge
 * and web search.
 */
export function onlyGranted(
  definition: AgentRunDefinition,
  granted: ReadonlyMap<string, ReadonlySet<string>>,
): AgentRunDefinition {
  const held = <
    T extends { tools: AgentRunDefinition['toolsets'][number]['tools'] },
  >(
    owner: T,
    instanceId: string,
  ): T[] => {
    const names = granted.get(instanceId);
    const tools = owner.tools.filter((tool) => names?.has(tool.name) === true);
    return tools.length === 0 ? [] : [{ ...owner, tools }];
  };
  return {
    ...definition,
    toolsets: definition.toolsets.flatMap((toolset) =>
      toolset.instanceId === null
        ? [toolset]
        : held(toolset, toolset.instanceId),
    ),
    mcpServers: definition.mcpServers.flatMap((server) =>
      held(server, server.instanceId),
    ),
    skills: definition.skills.filter((skill) =>
      granted.has(skillInstallationId(skill.name)),
    ),
  };
}

/**
 * The agent authority with the control plane watching (CPD-7, 05 §1 rule 6):
 * every definition admission receives, for a submit, a drain or any other
 * path that asks the authority, is compared with the control plane's
 * effective set for the same actor and agent. In `shadow` it is returned as
 * it came, and the comparison runs after the answer, so it costs the turn
 * nothing and never fails or changes it. With `CONTROL_PLANE_PIN=on` the
 * answer waits for it: an item the actor may not see (not org-visible, not
 * their team's, not theirs, not shared by the agent's owner) is taken out, as
 * CPS's rule takes out a skill (VIS-1), and when the control plane cannot
 * answer, admission fails closed. For an org the capability migration
 * recorded as migrated (CPD-10 R3) the effective set is the only authority
 * under `on`: everything it does not hold ready is taken out, so an empty set
 * grants no item. Both read the pinner's snapshot, which carries that fact.
 * The system assistant is not reviewed: no grant is migrated for it (CPD-7
 * open item 3), so every catalog item it has would read as a mismatch, and a
 * migrated org's empty set would take them all away.
 */
export class ControlPlaneReviewedAgentAccess implements AgentAccessPolicy {
  private readonly running = new Set<Promise<unknown>>();

  constructor(
    private readonly inner: AgentAccessPolicy,
    private readonly shadow: EffectiveSetShadow,
  ) {}

  async authorizeRun(
    request: AgentUseRequest,
  ): Promise<AgentRunDefinition | null> {
    const definition = await this.inner.authorizeRun(request);
    if (
      definition === null ||
      !this.shadow.active ||
      definition.agentKey === ASSISTANT_AGENT_DEFINITION_KEY
    )
      return definition;
    const reviewed = this.shadow.review(
      {
        orgId: request.orgId,
        actorId: request.actorId,
        agentKey: request.agentKey,
        agentOwner: definition.owner ?? null,
      },
      legacyPinOf(definition),
    );
    if (!this.shadow.enforcing) {
      this.running.add(reviewed);
      void reviewed.finally(() => this.running.delete(reviewed));
      return definition;
    }
    const { notVisible, granted } = await reviewed;
    if (notVisible === null) {
      throw new HarnessUnavailableError(
        'The control plane could not decide what this run may see',
      );
    }
    if (granted !== null) return onlyGranted(definition, granted);
    return notVisible.size === 0
      ? definition
      : withoutHidden(definition, notVisible);
  }

  /** Settles once every comparison started so far has been counted (tests, shutdown). */
  async idle(): Promise<void> {
    while (this.running.size > 0) await Promise.all([...this.running]);
  }
}
