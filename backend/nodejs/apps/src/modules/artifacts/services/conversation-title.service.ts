import { getConversationRepository } from '../../../libs/repositories/conversation';
import { isValidId } from '../../../libs/repositories/ids';
import { ProjectService } from '../../projects/services/project.service';

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
 * owner, and for `sharedWith` viewers of an assistant chat.
 */
export class ConversationTitleService {
  static async batchTitles(
    conversationIds: string[],
    orgId: string,
    userId: string,
  ): Promise<Map<string, ConversationSummary>> {
    const uniqueIds = [...new Set(conversationIds.filter(Boolean))];
    const validIds = uniqueIds.filter((id) => isValidId(id));
    if (!validIds.length) {
      return new Map();
    }

    const accessibleProjectIds = await ProjectService.getAccessibleProjectIds(
      orgId,
      userId,
    );

    const sessions = await getConversationRepository().listVisibleSummaries(
      orgId,
      userId,
      validIds,
      accessibleProjectIds.map(String),
    );

    const accessibleProjects = new Set(accessibleProjectIds.map(String));
    return new Map(
      sessions.map((session) => [
        session._id,
        {
          title: session.title ?? 'Untitled',
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
