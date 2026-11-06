import { getConversationRepository } from '../../../libs/repositories/conversation';
import { isValidId } from '../../../libs/repositories/ids';
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

/** The caller, and the projects the conversation guards found they can view. */
export interface ConversationTitleScope {
  orgId: string;
  userId: string;
  accessibleProjectIds: readonly string[];
}

/**
 * Display-only conversation titles for the artifacts gallery.
 * Authorization for the files themselves lives on the graph record ACL;
 * this join never grants access and omits titles the caller cannot see.
 *
 * The access predicate matches the conversation list: owner, a chat that is
 * both `isShared` and listed in `sharedWith` for this user, or a
 * project-visible chat in a project the caller can view. `isShared` alone is
 * not enough: unshare removes the viewer from `sharedWith` while leaving the
 * chat shared with others.
 *
 * An archived chat is listed only where the archive can open it: for its
 * owner, and for people an assistant chat is shared with.
 */
export class ConversationTitleService {
  static async batchTitles(
    conversationIds: string[],
    scope: ConversationTitleScope,
  ): Promise<Map<string, ConversationSummary>> {
    const uniqueIds = [...new Set(conversationIds.filter(Boolean))];
    const validIds = uniqueIds.filter((id) => isValidId(id));
    if (!validIds.length) {
      return new Map();
    }

    const sessions = await getConversationRepository().listVisibleSummaries(
      scope.orgId,
      scope.userId,
      validIds,
      scope.accessibleProjectIds.map(String),
    );

    const accessibleProjects = new Set(scope.accessibleProjectIds.map(String));
    return new Map(
      sessions.map((session) => [
        session._id,
        {
          title:
            conversationExtensions().views.displayTitle(session.title) ??
            'Untitled',
          ...(session.sessionType === 'agent' && session.agentKey
            ? { agentKey: session.agentKey }
            : {}),
          ...(session.projectId && accessibleProjects.has(session.projectId)
            ? { projectId: session.projectId }
            : {}),
          ...(session.isArchived
            ? { conversationArchived: true as const }
            : {}),
        },
      ]),
    );
  }
}
