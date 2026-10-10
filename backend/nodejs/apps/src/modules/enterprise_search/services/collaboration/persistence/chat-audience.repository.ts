import { ClientSession, Types } from 'mongoose';
import { ChatSession } from '../../../schema/chat.session.schema';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import { toCollaborator } from '../domain/collaborator.mapper';
import { ConversationRef } from '../domain/types';

/** The people a turn's activity can concern: the owner and the users with a direct `write` row. */
export interface ActivityAudience {
  orgId: string;
  ref: ConversationRef;
  userIds: readonly string[];
}

export interface IChatAudienceRepository {
  /** Null when the conversation is gone. Teams are not part of it. */
  activityAudience(
    orgId: string,
    sessionId: string,
  ): Promise<ActivityAudience | null>;
  /** Users who authored at least one `user_query` in the session. */
  authors(
    orgId: string,
    sessionId: string,
    opts?: { session?: ClientSession },
  ): Promise<readonly string[]>;
}

export class MongoChatAudienceRepository implements IChatAudienceRepository {
  async activityAudience(
    orgId: string,
    sessionId: string,
  ): Promise<ActivityAudience | null> {
    const session = await ChatSession.findOne({
      _id: new Types.ObjectId(sessionId),
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    })
      .select('orgId userId agentKey sharedWith')
      .lean()
      .exec();
    if (!session) {
      return null;
    }
    const writers = (session.sharedWith ?? []).flatMap((row) => {
      const c = toCollaborator(row);
      return c?.principal.type === 'user' && c.accessLevel === 'write'
        ? [c.principal.userId]
        : [];
    });
    const conversationId = sessionId;
    return {
      orgId: session.orgId.toString(),
      ref:
        session.agentKey !== undefined && session.agentKey !== ''
          ? { kind: 'agent', conversationId, agentKey: session.agentKey }
          : { kind: 'chat', conversationId },
      userIds: [...new Set([session.userId.toString(), ...writers])],
    };
  }

  async authors(
    orgId: string,
    sessionId: string,
    opts: { session?: ClientSession } = {},
  ): Promise<readonly string[]> {
    const ids = await ChatSessionMessage.distinct(
      'authorUserId',
      {
        orgId: new Types.ObjectId(orgId),
        sessionId: new Types.ObjectId(sessionId),
        messageType: 'user_query',
        authorUserId: { $type: 'objectId' },
      },
      opts.session ? { session: opts.session } : undefined,
    );
    return ids.map((id: Types.ObjectId) => id.toString());
  }
}
