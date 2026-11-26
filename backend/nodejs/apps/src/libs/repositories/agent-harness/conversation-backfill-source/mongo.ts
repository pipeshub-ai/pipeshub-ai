import { Types } from 'mongoose';
import { AgentRunModel } from '../execution/schema/execution.schema';
import { principalRefFor } from '../../../../modules/agent-harness/domain/run-spec';
import {
  BackfillCounts,
  ConversationObservation,
} from '../../../../modules/agent-harness/domain/erasure-backfill';
import { BackfillSource } from '../../../../modules/agent-harness/ports/erasure-backfill';
import { AgentConversation } from '../../../../modules/enterprise_search/schema/agent.conversation.schema';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../modules/enterprise_search/schema/chat.session.message.schema';
import { Conversation } from '../../../../modules/enterprise_search/schema/conversation.schema';

interface SessionRow {
  _id: Types.ObjectId;
  orgId: Types.ObjectId;
  isDeleted?: boolean;
  deletedBy?: Types.ObjectId | null;
  erasureRequestedAt?: number | null;
  updatedAt?: Date;
}

const PROJECTION = {
  _id: 1,
  orgId: 1,
  isDeleted: 1,
  deletedBy: 1,
  erasureRequestedAt: 1,
  updatedAt: 1,
} as const;

const objectIds = (ids: readonly string[]): Types.ObjectId[] =>
  ids
    .filter((id) => Types.ObjectId.isValid(id))
    .map((id) => new Types.ObjectId(id));

/**
 * Deleted conversations as the E7B backfill sees them: the session document's
 * deletion fields and how many documents each store holds for it, read in
 * one query per store per page.
 */
export class HarnessConversationBackfillSource implements BackfillSource {
  async deletedAfter(
    afterSessionId: string | null,
    limit: number,
    orgId: string | null,
  ): Promise<ConversationObservation[]> {
    if (orgId !== null && !Types.ObjectId.isValid(orgId)) return [];
    const rows = await ChatSession.find(
      {
        isDeleted: true,
        ...(orgId !== null ? { orgId: new Types.ObjectId(orgId) } : {}),
        ...(afterSessionId !== null && Types.ObjectId.isValid(afterSessionId)
          ? { _id: { $gt: new Types.ObjectId(afterSessionId) } }
          : {}),
      },
      PROJECTION,
    )
      .sort({ _id: 1 })
      .limit(limit)
      .read('primary')
      .lean<SessionRow[]>();
    return this.observations(rows);
  }

  async observe(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<ConversationObservation[]> {
    if (!Types.ObjectId.isValid(orgId) || sessionIds.length === 0) return [];
    const rows = await ChatSession.find(
      { _id: { $in: objectIds(sessionIds) }, orgId: new Types.ObjectId(orgId) },
      PROJECTION,
    )
      .sort({ _id: 1 })
      .read('primary')
      .lean<SessionRow[]>();
    return this.observations(rows);
  }

  private async observations(
    rows: readonly SessionRow[],
  ): Promise<ConversationObservation[]> {
    if (rows.length === 0) return [];
    const ids = rows.map((row) => row._id);
    const strings = ids.map(String);
    const [messages, conversations, agentConversations, runs] =
      await Promise.all([
        ChatSessionMessage.aggregate<{
          _id: Types.ObjectId;
          messages: number;
          citations: number;
        }>([
          { $match: { sessionId: { $in: ids } } },
          {
            $group: {
              _id: '$sessionId',
              messages: { $sum: 1 },
              citations: { $sum: { $size: { $ifNull: ['$citations', []] } } },
            },
          },
        ]).read('primary'),
        Conversation.find({ _id: { $in: ids } }, { _id: 1 })
          .read('primary')
          .lean(),
        AgentConversation.find({ _id: { $in: ids } }, { _id: 1 })
          .read('primary')
          .lean(),
        AgentRunModel.aggregate<{ _id: string; runs: number }>([
          { $match: { sessionId: { $in: strings } } },
          { $group: { _id: '$sessionId', runs: { $sum: 1 } } },
        ]).read('primary'),
      ]);
    const messagesBy = new Map(messages.map((row) => [String(row._id), row]));
    const legacyBy = new Map<string, number>();
    for (const row of [...conversations, ...agentConversations]) {
      const id = String(row._id);
      legacyBy.set(id, (legacyBy.get(id) ?? 0) + 1);
    }
    const runsBy = new Map(runs.map((row) => [row._id, row.runs]));
    return rows.map((row) => {
      const sessionId = String(row._id);
      const counts: BackfillCounts = {
        chatSessions: 1,
        chatSessionMessages: messagesBy.get(sessionId)?.messages ?? 0,
        citations: messagesBy.get(sessionId)?.citations ?? 0,
        legacyCopies: legacyBy.get(sessionId) ?? 0,
        agentRuns: runsBy.get(sessionId) ?? 0,
      };
      return {
        orgId: String(row.orgId),
        sessionId,
        deleted: row.isDeleted === true,
        erasureRequested:
          row.erasureRequestedAt !== undefined &&
          row.erasureRequestedAt !== null,
        by:
          row.deletedBy !== undefined && row.deletedBy !== null
            ? principalRefFor(String(row.deletedBy))
            : null,
        updatedAt: row.updatedAt instanceof Date ? row.updatedAt.getTime() : 0,
        counts,
      };
    });
  }
}
