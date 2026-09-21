import { Types } from 'mongoose';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';

/**
 * Display-only conversation titles for the artifacts gallery.
 * Authorization for the files themselves lives on the graph record ACL;
 * this join never grants access and omits titles the caller cannot see.
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

    const sessions = await ChatSession.find(
      {
        _id: { $in: objectIds },
        orgId: new Types.ObjectId(orgId),
        isDeleted: { $ne: true },
        $or: [
          { userId: new Types.ObjectId(userId) },
          { isShared: true },
          { 'sharedWith.userId': new Types.ObjectId(userId) },
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
