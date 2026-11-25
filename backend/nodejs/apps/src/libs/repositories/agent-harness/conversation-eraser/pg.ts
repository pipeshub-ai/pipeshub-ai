import { getConversationRepository } from '../../conversation';
import { getNotificationRepository } from '../../notification';
import type {
  AttachmentEraser,
  ConversationAttachment,
  ConversationEraser,
  ConversationErasure,
  ConversationState,
  ConversationStateLookup,
} from '../../../../modules/agent-harness/ports/session-erasure';
import { eraseConversationAttachments } from '../../../../modules/agent-harness/application/conversation-attachments';
import { hasValidIds } from '../chat-session-repository/pg';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';

const BATCH = 1_000;

const NOTHING: ConversationErasure = {
  citations: 0,
  messages: 0,
  sessions: 0,
  attachments: 0,
  attachmentBlobs: 0,
};

const chunks = <T>(items: readonly T[], size: number): T[][] =>
  Array.from({ length: Math.ceil(items.length / size) }, (_, index) =>
    items.slice(index * size, (index + 1) * size),
  );

/**
 * A conversation's own rows (E7 step `conversation`) on PostgreSQL, through the conversation and notification
 * repositories, in the order the MongoDB eraser keeps: the chat attachments its messages carry (through the query
 * service), the citations they point at, then the messages with the session, then the inbox notices that link to it.
 * Each step is idempotent, so a crash part-way is finished by the retry. No frozen legacy copy exists here.
 */
export class PgHarnessConversationEraser
  implements ConversationEraser, ConversationStateLookup
{
  constructor(private readonly attachments: AttachmentEraser) {}

  async state(orgId: string, sessionId: string): Promise<ConversationState> {
    if (!hasValidIds(orgId, sessionId)) return 'absent';
    const row = await inBoundOrgUnit(orgId, () =>
      getConversationRepository().findHarnessSession(sessionId, orgId),
    );
    if (row === null) return 'absent';
    return row.session.isDeleted === true ? 'deleted' : 'live';
  }

  erase(orgId: string, sessionId: string): Promise<ConversationErasure> {
    if (!hasValidIds(orgId, sessionId)) return Promise.resolve(NOTHING);
    return inBoundOrgUnit(orgId, async () => {
      const repo = getConversationRepository();
      const messages = (await repo.listMessages(sessionId, { orgId })).map(
        (row) => row.dto,
      );
      const owner = (await repo.findHarnessSession(sessionId, orgId))?.session
        .userId;
      // A collaborator's turn names its sender; the owner's names nobody.
      const listed: ConversationAttachment[] = messages.flatMap((message) => {
        const sender = message.senderId ?? owner;
        return sender === undefined
          ? []
          : (message.attachments ?? []).map((attachment) => ({
              recordId: attachment.recordId,
              ownerId: sender,
            }));
      });
      const attachments = await eraseConversationAttachments(
        this.attachments,
        orgId,
        listed,
        (recordIds) => repo.markAttachmentsRemoved(orgId, sessionId, recordIds),
      );
      const citationIds = [
        ...new Set(
          messages.flatMap((message) =>
            (message.citations ?? []).flatMap((citation) =>
              citation.citationId ? [citation.citationId] : [],
            ),
          ),
        ),
      ];
      let citations = 0;
      for (const batch of chunks(citationIds, BATCH)) {
        citations += await repo.deleteCitations(batch, orgId);
      }
      const removed = await repo.discardSession(sessionId, orgId);
      // A scheduled run's inbox notice links to its conversation by id (CLEANUP-3): it goes with it.
      await getNotificationRepository().eraseForConversation(orgId, sessionId);
      return {
        citations,
        messages: removed.messages,
        sessions: removed.sessions,
        ...attachments,
      };
    });
  }
}
