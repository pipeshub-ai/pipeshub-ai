import { getConversationRepository } from '../../../libs/repositories/conversation';
import { isValidId } from '../../../libs/repositories/ids';
import { ProjectService } from '../../projects/services/project.service';

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
 */
export class ConversationTitleService {
  static async batchTitles(
    conversationIds: string[],
    orgId: string,
    userId: string,
  ): Promise<Map<string, string>> {
    const uniqueIds = [...new Set(conversationIds.filter(Boolean))];
    const validIds = uniqueIds.filter((id) => isValidId(id));
    if (!validIds.length) {
      return new Map();
    }

    const accessibleProjectIds = await ProjectService.getAccessibleProjectIds(
      orgId,
      userId,
    );

    const sessions = await getConversationRepository().listVisibleTitles(
      orgId,
      userId,
      validIds,
      accessibleProjectIds.map(String),
    );

    return new Map(
      sessions.map((session) => [session._id, session.title ?? 'Untitled']),
    );
  }
}
