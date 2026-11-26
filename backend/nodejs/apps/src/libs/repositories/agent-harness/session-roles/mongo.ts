import { Types } from 'mongoose';
import {
  EXCLUDE_AGENT,
  ONLY_AGENT,
} from '../../../../modules/enterprise_search/constants/constants';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import {
  SessionParticipants,
  SessionRole,
  sessionRoleOf,
} from '../../../../modules/agent-harness/domain/session-roles';
import { SessionExecutionScope } from '../../../../modules/agent-harness/ports/chat-session-repository';
import { SessionRoleLookup } from '../../../../modules/agent-harness/ports/session-roles';

export interface ParticipantFields {
  userId?: unknown;
  isShared?: boolean;
  sharedWith?: Array<{ userId?: unknown; accessLevel?: unknown }>;
}

/** A conversation document's owner and shares, as the harness reads roles. */
export const sessionParticipantsOf = (
  session: ParticipantFields,
): SessionParticipants => ({
  ownerId: String(session.userId),
  isShared: session.isShared === true,
  shares: (session.sharedWith ?? []).map((share) => ({
    userId: String(share.userId),
    accessLevel: String(share.accessLevel),
  })),
});

/**
 * Roles read from the conversation document (chat or agent, legacy or
 * durable): `userId` is the owner, `sharedWith` the shares. One indexed read
 * by id, scoped to the org.
 */
export class HarnessSessionRoles implements SessionRoleLookup {
  async roleOf(scope: SessionExecutionScope): Promise<SessionRole | null> {
    if (
      ![scope.sessionId, scope.orgId, scope.actorId].every((id) =>
        Types.ObjectId.isValid(id),
      )
    ) {
      return null;
    }
    const session = await ChatSession.findOne({
      _id: scope.sessionId,
      orgId: scope.orgId,
      isDeleted: false,
      ...(scope.agentKey !== undefined && scope.agentKey.length > 0
        ? { agentKey: scope.agentKey, ...ONLY_AGENT }
        : EXCLUDE_AGENT),
    })
      .select('userId isShared sharedWith')
      .lean<ParticipantFields>();
    if (session === null) return null;
    return sessionRoleOf(sessionParticipantsOf(session), scope.actorId);
  }

  async isShared(scope: {
    orgId: string;
    sessionId: string;
  }): Promise<boolean> {
    if (
      ![scope.sessionId, scope.orgId].every((id) => Types.ObjectId.isValid(id))
    ) {
      return true;
    }
    const session = await ChatSession.findOne({
      _id: scope.sessionId,
      orgId: scope.orgId,
      isDeleted: false,
    })
      .select('userId isShared sharedWith')
      .lean<ParticipantFields>();
    if (session === null) return true;
    const participants = sessionParticipantsOf(session);
    return participants.isShared && participants.shares.length > 0;
  }
}
