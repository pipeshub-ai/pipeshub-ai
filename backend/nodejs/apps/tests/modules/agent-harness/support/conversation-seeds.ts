/**
 * Conversations seeded the way the conversation module writes them: on MongoDB
 * through its own models, in memory through `InMemoryConversations`. The
 * erasure, retention and backfill suites share them.
 */
import mongoose, { Types } from 'mongoose';
import { InMemoryConversations } from '../../../../src/modules/agent-harness/adapters/in-memory-conversations';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema';

export const CONVERSATION_SENTINEL = 'SENTINEL-backfill-content';

/** One conversation to seed: ids are ObjectId hex so both adapters take them. */
export interface SeedConversation {
  orgId: string;
  sessionId: string;
  deleted: boolean;
  /** A delete of this release (`erasureRequestedAt`). */
  marked?: boolean;
  /** When the delete asked for erasure; implies `marked`. */
  requestedAt?: number;
  /** The deleting user's id (hex); the observation's `by` is its principal ref. */
  deletedBy?: string | null;
  lastActivityAt?: number;
  messages?: number;
  /** Citations per message. */
  citations?: number;
  legacyCopies?: number;
  runs?: number;
  /** The session owner's user id (hex); random when absent. */
  ownerId?: string;
  /** One user turn per attachment; `senderId` (hex) is a collaborator's turn, absent the owner's. */
  attachments?: Array<{ recordId: string; senderId?: string }>;
}

/** The seeded citations' record ids name their conversation, so a probe can count them after the messages go. */
export const citationRecordPrefix = (sessionId: string): string => `rec-${sessionId}-`;

const requestedAt = (c: SeedConversation, marked: () => number): number | undefined =>
  c.requestedAt ?? (c.marked ? marked() : undefined);

export function seedInMemoryConversations(
  conversations: InMemoryConversations,
  seeded: SeedConversation[],
): void {
  for (const c of seeded) {
    const erasureRequestedAt = requestedAt(c, () => 1);
    conversations.put({
      orgId: c.orgId,
      sessionId: c.sessionId,
      deleted: c.deleted,
      ...(erasureRequestedAt !== undefined ? { erasureRequestedAt } : {}),
      by: c.deletedBy ? `p_${c.deletedBy}` : null,
      updatedAt: 10,
      lastActivityAt: c.lastActivityAt ?? 10,
      counts: {
        chatSessionMessages: (c.messages ?? 0) + (c.attachments?.length ?? 0),
        citations: (c.messages ?? 0) * (c.citations ?? 0),
        legacyCopies: c.legacyCopies ?? 0,
        agentRuns: c.runs ?? 0,
      },
      attachments: (c.attachments ?? []).map(({ recordId, senderId }) => ({
        recordId,
        ownerId: senderId ?? c.ownerId ?? 'owner',
      })),
    });
  }
}

export async function seedMongoConversations(conversations: SeedConversation[]): Promise<void> {
  for (const c of conversations) {
    const userId = new Types.ObjectId(c.ownerId);
    const erasureRequestedAt = requestedAt(c, Date.now);
    const session = await ChatSession.create({
      _id: new Types.ObjectId(c.sessionId),
      userId,
      orgId: new Types.ObjectId(c.orgId),
      initiator: userId,
      title: CONVERSATION_SENTINEL,
      isDeleted: c.deleted,
      ...(c.deletedBy ? { deletedBy: new Types.ObjectId(c.deletedBy) } : {}),
      ...(erasureRequestedAt !== undefined ? { erasureRequestedAt } : {}),
      ...(c.lastActivityAt !== undefined ? { lastActivityAt: c.lastActivityAt } : {}),
    });
    for (let seq = 0; seq < (c.messages ?? 0); seq += 1) {
      const citations = await Citation.create(
        Array.from({ length: c.citations ?? 0 }, (_unused, i) => ({
          content: `${CONVERSATION_SENTINEL} ${String(i)}`,
          chunkIndex: i,
          citationType: 'vectordb|document',
          metadata: { orgId: c.orgId, mimeType: 'text/plain', recordId: `${citationRecordPrefix(c.sessionId)}${String(i)}`, recordName: 'r', origin: 'UPLOAD', extension: 'txt' },
        })),
      );
      await ChatSessionMessage.create({
        sessionId: session._id,
        orgId: new Types.ObjectId(c.orgId),
        seq,
        messageType: 'bot_response',
        content: CONVERSATION_SENTINEL,
        citations: citations.map((citation) => ({ citationId: citation._id })),
      });
    }
    for (const [index, { recordId, senderId }] of (c.attachments ?? []).entries()) {
      await ChatSessionMessage.create({
        sessionId: session._id,
        orgId: new Types.ObjectId(c.orgId),
        seq: (c.messages ?? 0) + index,
        messageType: 'user_query',
        content: CONVERSATION_SENTINEL,
        attachments: [{ recordId, recordName: `${CONVERSATION_SENTINEL}.txt`, mimeType: 'text/plain' }],
        ...(senderId !== undefined ? { senderId: new Types.ObjectId(senderId) } : {}),
      });
    }
    for (let copy = 0; copy < (c.legacyCopies ?? 0); copy += 1) {
      await mongoose.connection
        .collection(copy === 0 ? 'conversations' : 'agentconversations')
        .insertOne({ _id: session._id, orgId: new Types.ObjectId(c.orgId), messages: [{ content: CONVERSATION_SENTINEL }] });
    }
    for (let run = 0; run < (c.runs ?? 0); run += 1) {
      await mongoose.connection.collection('agentRuns').insertOne({ orgId: c.orgId, sessionId: c.sessionId, runId: `run-${c.sessionId}-${String(run)}`, status: 'completed' });
    }
  }
}
