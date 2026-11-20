import { QueryFilter, Types } from 'mongoose';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';
import { IChatSession } from '../../enterprise_search/types/conversation.interfaces';
import { conversationExtensions } from '../../enterprise_search/services/conversation/conversation-extensions';

/** What the artifacts gallery shows about an artifact's conversation. */
export interface ConversationSummary {
  title: string;
  /** Set for an agent chat, so the gallery can open it under its agent. */
  agentKey?: string;
  /** Set for a chat in a project the caller can view, so it opens inside that project. */
  projectId?: string;
  /** Set when the chat is archived; it then opens from the archived chats page. */
  conversationArchived?: true;
}

/** The caller's conversation list scope, as the conversation guards resolved it. */
export interface ConversationTitleScope {
  readFilter: QueryFilter<IChatSession>;
  userId: string;
  accessibleProjectIds: readonly string[];
}

/**
 * Display-only conversation titles for the artifacts gallery. The join never
 * grants access: a title is returned only for a conversation `readFilter`
 * matches, which is the conversation guards' list filter for the caller.
 *
 * An archived chat is listed only where the archive can open it: for its
 * owner, and for people an assistant chat is shared with.
 */
export class ConversationTitleService {
  static async batchTitles(
    conversationIds: string[],
    scope: ConversationTitleScope,
  ): Promise<Map<string, ConversationSummary>> {
    const objectIds = [...new Set(conversationIds.filter(Boolean))]
      .filter((id) => Types.ObjectId.isValid(id))
      .map((id) => new Types.ObjectId(id));
    if (!objectIds.length) {
      return new Map();
    }

    const userObjectId = new Types.ObjectId(scope.userId);
    const sessions = await ChatSession.find(
      {
        $and: [
          scope.readFilter,
          { _id: { $in: objectIds } },
          {
            $or: [
              { isArchived: { $ne: true } },
              { userId: userObjectId },
              {
                sessionType: { $ne: 'agent' },
                'sharedWith.userId': userObjectId,
              },
            ],
          },
          // A Compare models branch is opened by its id, never listed.
          { compareOf: { $exists: false } },
        ],
      },
      {
        _id: 1,
        title: 1,
        sessionType: 1,
        agentKey: 1,
        projectId: 1,
        isArchived: 1,
      },
    ).lean();

    const accessibleProjects = new Set(scope.accessibleProjectIds.map(String));
    return new Map(
      sessions.map((session) => [
        String(session._id),
        {
          title:
            conversationExtensions().views.displayTitle(session.title) ??
            'Untitled',
          ...(session.sessionType === 'agent' && session.agentKey
            ? { agentKey: session.agentKey }
            : {}),
          ...(session.projectId &&
          accessibleProjects.has(String(session.projectId))
            ? { projectId: String(session.projectId) }
            : {}),
          ...(session.isArchived
            ? { conversationArchived: true as const }
            : {}),
        },
      ]),
    );
  }
}
