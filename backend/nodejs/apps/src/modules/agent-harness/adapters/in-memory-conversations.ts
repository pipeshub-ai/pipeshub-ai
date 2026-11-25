/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements asynchronous ports without I/O. */
import {
  BackfillCounts,
  ConversationObservation,
  zeroBackfillCounts,
} from '../domain/erasure-backfill';
import { BackfillSource } from '../ports/erasure-backfill';
import {
  AttachmentEraser,
  ConversationAttachment,
  ConversationEraser,
  ConversationErasure,
  ConversationState,
  ConversationStateLookup,
} from '../ports/session-erasure';
import {
  MarkAttachmentsRemoved,
  eraseConversationAttachments,
} from '../application/conversation-attachments';
import {
  AwaitingErasure,
  ConversationRetention,
  ErasureRequestCursor,
} from '../ports/session-retention';

export interface InMemoryConversation {
  orgId: string;
  sessionId: string;
  deleted: boolean;
  /** Set by a delete that asked for erasure. */
  erasureRequestedAt?: number;
  /** Principal ref of who deleted it. */
  by?: string | null;
  updatedAt: number;
  lastActivityAt: number;
  counts?: Partial<BackfillCounts>;
  /**
   * The chat attachments its messages carry (one message each), each with the
   * message's sender; `removed` once another conversation's erasure took it.
   */
  attachments?: Array<ConversationAttachment & { removed?: boolean }>;
}

/**
 * The conversation module's erasure-facing ports in memory, for tests and
 * single-process runs; the Mongo adapters in `enterprise_search/adapters`
 * answer the same contracts.
 */
export class InMemoryConversations
  implements
    BackfillSource,
    ConversationStateLookup,
    ConversationEraser,
    ConversationRetention<unknown>
{
  private readonly sessions = new Map<string, InMemoryConversation>();

  constructor(
    private readonly clock: () => number = Date.now,
    private readonly attachments: AttachmentEraser | null = null,
  ) {}

  put(conversation: InMemoryConversation): void {
    this.sessions.set(conversation.sessionId, { ...conversation });
  }

  get(sessionId: string): InMemoryConversation | undefined {
    const found = this.sessions.get(sessionId);
    return found === undefined ? undefined : { ...found };
  }

  /** A change to the session document, as a write would make it. */
  update(sessionId: string, change: Partial<InMemoryConversation>): void {
    const found = this.sessions.get(sessionId);
    if (found === undefined) throw new Error(`no conversation ${sessionId}`);
    this.sessions.set(sessionId, {
      ...found,
      ...change,
      updatedAt: change.updatedAt ?? found.updatedAt + 1,
    });
  }

  async deletedAfter(
    afterSessionId: string | null,
    limit: number,
    orgId: string | null,
  ): Promise<ConversationObservation[]> {
    return [...this.sessions.values()]
      .filter(
        (conversation) =>
          conversation.deleted &&
          (orgId === null || conversation.orgId === orgId) &&
          (afterSessionId === null || conversation.sessionId > afterSessionId),
      )
      .sort((a, b) => (a.sessionId < b.sessionId ? -1 : 1))
      .slice(0, limit)
      .map((conversation) => this.observation(conversation));
  }

  async observe(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<ConversationObservation[]> {
    return sessionIds.flatMap((sessionId) => {
      const conversation = this.sessions.get(sessionId);
      return conversation?.orgId !== orgId
        ? []
        : [this.observation(conversation)];
    });
  }

  async state(orgId: string, sessionId: string): Promise<ConversationState> {
    const conversation = this.sessions.get(sessionId);
    if (conversation?.orgId !== orgId) {
      return 'absent';
    }
    return conversation.deleted ? 'deleted' : 'live';
  }

  async erase(orgId: string, sessionId: string): Promise<ConversationErasure> {
    const conversation = this.sessions.get(sessionId);
    if (conversation?.orgId !== orgId) {
      return {
        citations: 0,
        messages: 0,
        sessions: 0,
        attachments: 0,
        attachmentBlobs: 0,
      };
    }
    const listed = conversation.attachments ?? [];
    if (listed.length > 0 && this.attachments === null) {
      throw new Error('Conversation attachments need an attachment eraser');
    }
    const attachments =
      this.attachments === null
        ? { attachments: 0, attachmentBlobs: 0 }
        : await eraseConversationAttachments(
            this.attachments,
            orgId,
            listed,
            this.markRemovedElsewhere(orgId, sessionId),
          );
    this.sessions.delete(sessionId);
    const counts = this.counts(conversation);
    return {
      citations: counts.citations,
      messages: counts.chatSessionMessages,
      sessions: 1,
      ...attachments,
    };
  }

  async idleSince(
    orgId: string,
    before: number,
    limit: number,
  ): Promise<string[]> {
    return [...this.sessions.values()]
      .filter(
        (conversation) =>
          conversation.orgId === orgId &&
          !conversation.deleted &&
          conversation.lastActivityAt < before,
      )
      .sort((a, b) => a.lastActivityAt - b.lastActivityAt)
      .slice(0, limit)
      .map((conversation) => conversation.sessionId);
  }

  async expire(
    orgId: string,
    sessionId: string,
    before: number,
  ): Promise<boolean> {
    const conversation = this.sessions.get(sessionId);
    if (
      conversation?.orgId !== orgId ||
      conversation.deleted ||
      conversation.lastActivityAt >= before
    ) {
      return false;
    }
    this.update(sessionId, {
      deleted: true,
      erasureRequestedAt: this.clock(),
    });
    return true;
  }

  async awaitingErasure(
    after: ErasureRequestCursor | null,
    limit: number,
  ): Promise<AwaitingErasure[]> {
    const key = (requestedAt: number, sessionId: string): string =>
      `${String(requestedAt).padStart(16, '0')}:${sessionId}`;
    return [...this.sessions.values()]
      .flatMap((conversation) =>
        conversation.deleted && conversation.erasureRequestedAt !== undefined
          ? [
              {
                orgId: conversation.orgId,
                sessionId: conversation.sessionId,
                requestedAt: conversation.erasureRequestedAt,
                by: conversation.by ?? null,
              },
            ]
          : [],
      )
      .filter(
        (awaiting) =>
          after === null ||
          key(awaiting.requestedAt, awaiting.sessionId) >
            key(after.requestedAt, after.sessionId),
      )
      .sort((a, b) =>
        key(a.requestedAt, a.sessionId) < key(b.requestedAt, b.sessionId)
          ? -1
          : 1,
      )
      .slice(0, limit);
  }

  private markRemovedElsewhere(
    orgId: string,
    sessionId: string,
  ): MarkAttachmentsRemoved {
    return async (recordIds) => {
      const gone = new Set(recordIds);
      let marked = 0;
      for (const conversation of this.sessions.values()) {
        if (
          conversation.orgId !== orgId ||
          conversation.sessionId === sessionId
        ) {
          continue;
        }
        conversation.attachments = conversation.attachments?.map(
          (attachment) => {
            if (attachment.removed === true || !gone.has(attachment.recordId)) {
              return attachment;
            }
            marked += 1;
            return { ...attachment, removed: true };
          },
        );
      }
      return marked;
    };
  }

  private observation(
    conversation: InMemoryConversation,
  ): ConversationObservation {
    return {
      orgId: conversation.orgId,
      sessionId: conversation.sessionId,
      deleted: conversation.deleted,
      erasureRequested: conversation.erasureRequestedAt !== undefined,
      by: conversation.by ?? null,
      updatedAt: conversation.updatedAt,
      counts: this.counts(conversation),
    };
  }

  private counts(conversation: InMemoryConversation): BackfillCounts {
    return { ...zeroBackfillCounts(), chatSessions: 1, ...conversation.counts };
  }
}
