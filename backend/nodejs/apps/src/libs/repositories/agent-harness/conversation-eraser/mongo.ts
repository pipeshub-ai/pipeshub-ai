import mongoose, { Types } from 'mongoose';
import {
  AttachmentEraser,
  ConversationAttachment,
  ConversationEraser,
  ConversationErasure,
  ConversationState,
  ConversationStateLookup,
} from '../../../../modules/agent-harness/ports/session-erasure';
import {
  MarkAttachmentsRemoved,
  eraseConversationAttachments,
} from '../../../../modules/agent-harness/application/conversation-attachments';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../../modules/enterprise_search/schema/citation.schema';
import { Conversation } from '../../../../modules/enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../../modules/enterprise_search/schema/agent.conversation.schema';
import { Notifications } from '../../../../modules/notification/schema/notification.schema';

const BATCH = 1_000;
const MAJORITY = { writeConcern: { w: 'majority' as const } };

const chunks = <T>(items: readonly T[], size: number): T[][] =>
  Array.from({ length: Math.ceil(items.length / size) }, (_, index) =>
    items.slice(index * size, (index + 1) * size),
  );

const markRemovedElsewhere =
  (org: Types.ObjectId, session: Types.ObjectId): MarkAttachmentsRemoved =>
  async (recordIds) => {
    const ids = [...recordIds];
    const marked = await ChatSessionMessage.updateMany(
      {
        orgId: org,
        sessionId: { $ne: session },
        attachments: {
          $elemMatch: { recordId: { $in: ids }, removed: { $ne: true } },
        },
      },
      { $set: { 'attachments.$[gone].removed': true } },
      { arrayFilters: [{ 'gone.recordId': { $in: ids } }], ...MAJORITY },
    );
    return marked.modifiedCount;
  };

/**
 * A conversation's own documents (E7 step `conversation`): the chat
 * attachments its messages carry (through the query service, QA15-F4), the
 * citations they point at, the messages, the session, and the frozen legacy
 * copy the chatSessions migration left under the same id. Attachments and
 * citations first, so a crash before the messages go leaves the message list
 * to find them again. An erased upload another conversation's message also
 * names is marked removed there (QA-15G), so that chip reads as removed rather
 * than as a link to nothing.
 */
export class HarnessConversationEraser
  implements ConversationEraser, ConversationStateLookup
{
  constructor(private readonly attachments: AttachmentEraser) {}

  /** The org's session as erasure must see it before each step; the frozen legacy copies follow it. */
  async state(orgId: string, sessionId: string): Promise<ConversationState> {
    if (!Types.ObjectId.isValid(sessionId) || !Types.ObjectId.isValid(orgId)) {
      return 'absent';
    }
    const session = await ChatSession.findOne(
      { _id: new Types.ObjectId(sessionId), orgId: new Types.ObjectId(orgId) },
      { isDeleted: 1 },
    )
      .read('primary')
      .lean();
    if (session === null) return 'absent';
    return session.isDeleted === true ? 'deleted' : 'live';
  }

  async erase(orgId: string, sessionId: string): Promise<ConversationErasure> {
    if (!Types.ObjectId.isValid(sessionId) || !Types.ObjectId.isValid(orgId)) {
      return {
        citations: 0,
        messages: 0,
        sessions: 0,
        attachments: 0,
        attachmentBlobs: 0,
      };
    }
    const session = new Types.ObjectId(sessionId);
    const org = new Types.ObjectId(orgId);
    const messages = await ChatSessionMessage.find(
      { sessionId: session, orgId: org },
      { citations: 1, attachments: 1, senderId: 1 },
    )
      .read('primary')
      .lean();
    const owner = await ChatSession.findOne(
      { _id: session, orgId: org },
      { userId: 1 },
    )
      .read('primary')
      .lean();
    // A collaborator's turn names its sender; the owner's names nobody.
    const listed: ConversationAttachment[] = messages.flatMap((message) => {
      const sender = message.senderId ?? owner?.userId;
      return sender === undefined
        ? []
        : (message.attachments ?? []).map((attachment) => ({
            recordId: attachment.recordId,
            ownerId: String(sender),
          }));
    });
    const attachments = await eraseConversationAttachments(
      this.attachments,
      orgId,
      listed,
      markRemovedElsewhere(org, session),
    );
    const citationIds = [
      ...new Set(
        messages.flatMap((message) =>
          (message.citations ?? []).flatMap((citation) =>
            citation.citationId ? [String(citation.citationId)] : [],
          ),
        ),
      ),
    ];
    let citations = 0;
    for (const batch of chunks(citationIds, BATCH)) {
      // Citations carry their org only in metadata; it still pins the delete to the tenant.
      const deleted = await Citation.deleteMany(
        {
          _id: { $in: batch.map((id) => new mongoose.Types.ObjectId(id)) },
          'metadata.orgId': orgId,
        },
        MAJORITY,
      );
      citations += deleted.deletedCount;
    }
    const removedMessages = await ChatSessionMessage.deleteMany(
      { sessionId: session, orgId: org },
      MAJORITY,
    );
    const sessions = await ChatSession.deleteOne(
      { _id: session, orgId: org },
      MAJORITY,
    );
    await Conversation.deleteOne({ _id: session, orgId: org }, MAJORITY);
    await AgentConversation.deleteOne({ _id: session, orgId: org }, MAJORITY);
    // A scheduled run's inbox notice links to its conversation by id (CLEANUP-3): it goes with it.
    await Notifications.deleteMany(
      { orgId: org, 'payload.conversationId': sessionId },
      MAJORITY,
    );
    return {
      citations,
      messages: removedMessages.deletedCount,
      sessions: sessions.deletedCount,
      ...attachments,
    };
  }
}
