import type { AgentAccessPolicy } from '../ports/agent-access';
import type { ActorAuthority, AgentVersionResolver } from '../ports/schedules';

/**
 * The version a schedule pins, asked of the agent authority exactly as a
 * firing asks it (OWD-14): with a short-lived credential of the actor, so a
 * schedule pins only a version its owner may run now.
 */
export class ActorAgentVersions implements AgentVersionResolver {
  constructor(
    private readonly actors: Pick<ActorAuthority, 'authorize'>,
    private readonly access: Pick<AgentAccessPolicy, 'authorizeRun'>,
  ) {}

  async resolve(
    orgId: string,
    actorId: string,
    agentKey: string,
    requested?: number,
  ): Promise<number | null> {
    const actor = await this.actors.authorize(orgId, actorId);
    if ('refused' in actor) return null;
    const definition = await this.access.authorizeRun({
      orgId,
      actorId,
      agentKey,
      authorization: actor.authorization,
      ...(requested !== undefined ? { agentVersion: requested } : {}),
    });
    return definition?.definitionVersion ?? null;
  }
}
