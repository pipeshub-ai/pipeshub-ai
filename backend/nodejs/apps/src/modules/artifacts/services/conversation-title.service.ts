import { displayTitle } from '../../enterprise_search/services/collaboration/mentions/mention.parser';
import { FilterQuery, Types } from 'mongoose';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';
import { IChatSession } from '../../enterprise_search/types/conversation.interfaces';

/**
 * Display-only conversation titles for the artifacts gallery. The join never
 * grants access: a title is returned only for a conversation `readFilter`
 * matches, which is the conversation guards' list filter for the caller (owner,
 * share rows including teams, project chats under the project ceiling).
 */
export class ConversationTitleService {
  static async batchTitles(
    conversationIds: string[],
    readFilter: FilterQuery<IChatSession>,
  ): Promise<Map<string, string>> {
    const objectIds = [...new Set(conversationIds.filter(Boolean))]
      .filter((id) => Types.ObjectId.isValid(id))
      .map((id) => new Types.ObjectId(id));
    if (!objectIds.length) {
      return new Map();
    }

    const sessions = await ChatSession.find(
      { $and: [readFilter, { _id: { $in: objectIds } }] },
      { _id: 1, title: 1 },
    ).lean();

    return new Map(
      (sessions || []).map((session) => [
        String(session._id),
        displayTitle(session.title) ?? 'Untitled',
      ]),
    );
  }
}
