import { Types } from 'mongoose';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';
import { ProjectService } from '../../projects/services/project.service';

/**
 * Display-only conversation titles for the artifacts gallery.
 * Authorization for the files themselves lives on the graph record ACL;
 * this join never grants access and omits titles the caller cannot see.
 *
 * The access predicate matches `buildFilter`: owner, a chat that is both
 * `isShared` and listed in `sharedWith` for this user, or a project-visible
 * chat in a project the caller can view. `isShared` alone is not enough —
 * unshare removes the viewer from `sharedWith` while leaving the chat shared
 * with others.
 */
export class ConversationTitleService {
  static async batchTitles(
    conversationIds: string[],
    orgId: string,
    userId: string,
  ): Promise<Map<string, string>> {
    const uniqueIds = [...new Set(conversationIds.filter(Boolean))];
    const objectIds = uniqueIds
      .filter((id) => Types.ObjectId.isValid(id))
      .map((id) => new Types.ObjectId(id));
    if (!objectIds.length) {
      return new Map();
    }

    const userObjectId = new Types.ObjectId(userId);
    const accessibleProjectIds = await ProjectService.getAccessibleProjectIds(
      orgId,
      userId,
    );

    const sessions = await ChatSession.find(
      {
        _id: { $in: objectIds },
        orgId: new Types.ObjectId(orgId),
        isDeleted: { $ne: true },
        $or: [
          { userId: userObjectId },
          {
            $and: [{ isShared: true }, { 'sharedWith.userId': userObjectId }],
          },
          ...(accessibleProjectIds.length > 0
            ? [
                {
                  projectId: { $in: accessibleProjectIds },
                  projectVisibility: 'project' as const,
                },
              ]
            : []),
        ],
      },
      { _id: 1, title: 1 },
    ).lean();

    return new Map(
      (sessions || []).map((session) => [
        String(session._id),
        session.title ?? 'Untitled',
      ]),
    );
  }
}
