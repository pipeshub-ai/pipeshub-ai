import type { AuthzActor } from '../ports/authorizer';
import type {
  AgentDirectory,
  AgentRunAccess,
  MembershipPort,
} from '../ports/directory';
import type { TeamMembership } from '../domain/resolution';

/**
 * The graph directory where no client to it is composed yet: every read
 * throws, so team rules deny and admission fails closed (03-interfaces §5.5),
 * never an empty answer that would read as "in no team".
 */
export class UnreadableDirectory implements MembershipPort, AgentDirectory {
  teamsOf(_orgId: string, _userId: string): Promise<readonly TeamMembership[]> {
    return Promise.reject(new Error('team membership is not readable here'));
  }

  authorizeRun(
    _orgId: string,
    _agentKey: string,
    _actor: AuthzActor,
  ): Promise<AgentRunAccess | null> {
    return Promise.reject(
      new Error('the agent directory is not readable here'),
    );
  }

  canEdit(
    _orgId: string,
    _agentKey: string,
    _actor: AuthzActor,
  ): Promise<boolean> {
    return Promise.reject(
      new Error('the agent directory is not readable here'),
    );
  }

  ownerTeam(_orgId: string, _agentKey: string): Promise<string | null> {
    return Promise.reject(
      new Error('the agent directory is not readable here'),
    );
  }
}
