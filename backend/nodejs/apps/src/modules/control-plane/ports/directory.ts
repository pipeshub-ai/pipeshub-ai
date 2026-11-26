import type { TeamMembership } from '../domain/resolution';
import type { AuthzActor } from './authorizer';

/** Team membership from the graph (03-interfaces §5.5). Unreadable membership throws; it is never `[]`. */
export interface MembershipPort {
  teamsOf(orgId: string, userId: string): Promise<readonly TeamMembership[]>;
  /** How many people administer (OWNER) the org's team; a team of another org has none. Unreadable throws. */
  teamOwnerCount?(orgId: string, teamId: string): Promise<number>;
  /** AP-08: who administers (OWNER) the org's team, by user id; a team of another org has nobody. Unreadable throws. */
  teamOwnerIds?(orgId: string, teamId: string): Promise<readonly string[]>;
}

export interface AgentRunAccess {
  readonly agentKey: string;
  readonly definitionVersion: number;
}

/**
 * The agent definitions the graph owns. A denial is `null` (or `false`); an
 * unreachable directory throws, so admission fails closed.
 */
export interface AgentDirectory {
  authorizeRun(
    orgId: string,
    agentKey: string,
    actor: AuthzActor,
  ): Promise<AgentRunAccess | null>;
  canEdit(orgId: string, agentKey: string, actor: AuthzActor): Promise<boolean>;
  ownerTeam(orgId: string, agentKey: string): Promise<string | null>;
}
