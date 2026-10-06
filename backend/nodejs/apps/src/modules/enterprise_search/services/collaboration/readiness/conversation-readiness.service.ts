import { Logger } from '../../../../../libs/services/logger.service';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { IProjectAccessPort } from '../../../../authz/ports/project-access.port';
import { ITeamDirectory } from '../../../../user_management/services/team-directory.service';
import { Readiness, ReadinessReason } from '../domain/collaboration-views';
import { COLLAB_ERROR_CODES } from '../domain/errors';
import { ConversationAccessGrant } from '../http/conversation-context';
import { OwnerActivity } from '../http/owner-activity';
import { IAgentReadinessPort } from './agent-readiness.port';

const defaultLogger = Logger.getInstance({ service: 'ConversationReadiness' });

export interface IConversationReadinessService {
  evaluate(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
  ): Promise<Readiness>;
}

/**
 * Whether the caller could send right now, for the composer. UX only: every turn still passes
 * the real guards and Python's own checks. Answers are about the caller alone (F-15).
 */
export class ConversationReadinessService
  implements IConversationReadinessService
{
  constructor(
    private readonly agents: IAgentReadinessPort,
    private readonly projects: IProjectAccessPort,
    private readonly teams: ITeamDirectory,
    private readonly ownerActivity: OwnerActivity,
    private readonly logger: Pick<Logger, 'warn'> = defaultLogger,
  ) {}

  async evaluate(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
  ): Promise<Readiness> {
    if (!grant.view.canSend) {
      return {
        canSend: false,
        reasons: [COLLAB_ERROR_CODES.READ_ONLY],
      };
    }
    const reasons: ReadinessReason[] = [];
    if (await this.ownerIsInactive(grant)) {
      reasons.push(COLLAB_ERROR_CODES.OWNER_INACTIVE);
    }
    if (await this.lacksProjectAccess(grant, identity)) {
      reasons.push(COLLAB_ERROR_CODES.PROJECT_ACCESS_REQUIRED);
    }
    const agent = await this.agentState(grant);
    if (agent.status === 'blocked') {
      reasons.push(COLLAB_ERROR_CODES.CONNECTOR_SETUP_REQUIRED);
    } else if (agent.status === 'unavailable') {
      reasons.push('AGENT_UNAVAILABLE');
    }
    return {
      canSend: reasons.length === 0,
      reasons,
      ...(agent.status === 'blocked' && { missingToolsets: agent.toolsets }),
    };
  }

  private async ownerIsInactive(
    grant: ConversationAccessGrant,
  ): Promise<boolean> {
    const ownerId = grant.session.userId.toString();
    if (ownerId === grant.caller.userId) {
      return false;
    }
    try {
      return !(await this.ownerActivity.isActive(grant.caller.orgId, ownerId));
    } catch (error) {
      this.logger.warn('Owner status unavailable; readiness skips it', {
        error: error instanceof Error ? error.message : String(error),
      });
      return false;
    }
  }

  /** The sender needs project access themself even when the owner linked the project (D7). */
  private async lacksProjectAccess(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
  ): Promise<boolean> {
    const projectId = grant.session.projectId?.toString();
    if (projectId === undefined) {
      return false;
    }
    const { userId, orgId } = grant.caller;
    let teamIds = grant.caller.teamIds;
    if (teamIds === 'unresolved') {
      const resolved = await this.teams.callerTeamIds(identity);
      if (resolved.status !== 'ok') {
        return false;
      }
      teamIds = resolved.teamIds;
    }
    return (
      (await this.projects.roleOf({ userId, orgId, teamIds }, projectId)) ===
      null
    );
  }

  /**
   * A cached `blocked` may predate the user connecting a tool, so it is dropped and asked again once;
   * Python's answer then replaces the cache (PH-05 carry-over).
   */
  private async agentState(
    grant: ConversationAccessGrant,
  ): ReturnType<IAgentReadinessPort['check']> {
    const agentKey = grant.session.agentKey;
    if (agentKey === undefined) {
      return { status: 'unknown' };
    }
    const subject = { orgId: grant.caller.orgId, userId: grant.caller.userId };
    const first = await this.agents.check(subject, agentKey);
    if (first.status !== 'blocked') {
      return first;
    }
    this.agents.invalidate(subject, agentKey);
    return this.agents.check(subject, agentKey);
  }
}
