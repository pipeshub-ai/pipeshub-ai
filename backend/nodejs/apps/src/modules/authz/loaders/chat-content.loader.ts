import { Types } from 'mongoose';
import { ChatSessionMessage } from '../../enterprise_search/schema/chat.session.message.schema';
import { AttachmentTurnRow, IChatContentLoader, RunTurn } from '../ports';

export const ATTACHMENT_CONTEXT_LIMIT = 20;

// The explicit `$type` is what makes a query eligible for the partial indexes on these fields; a bare equality is not.

interface TurnDoc {
  sessionId: Types.ObjectId;
  authorUserId?: Types.ObjectId;
  filesShared?: boolean;
  shareToolResults?: boolean;
}

export class ChatContentLoader implements IChatContentLoader {
  async loadAttachmentContext(
    orgId: string,
    recordId: string,
    conversationId?: string,
  ): Promise<AttachmentTurnRow[]> {
    if (
      !Types.ObjectId.isValid(orgId) ||
      (conversationId !== undefined && !Types.ObjectId.isValid(conversationId))
    ) {
      return [];
    }
    const rows = await ChatSessionMessage.find({
      orgId,
      messageType: 'user_query',
      'attachments.recordId': { $eq: recordId, $type: 'string' },
      ...(conversationId !== undefined && { sessionId: conversationId }),
    })
      .sort({ _id: -1 })
      .limit(ATTACHMENT_CONTEXT_LIMIT)
      .select('sessionId authorUserId filesShared')
      .lean<TurnDoc[]>();
    return rows.map((row) => ({
      sessionId: row.sessionId.toString(),
      ...(row.authorUserId && { authorUserId: row.authorUserId.toString() }),
      ...(row.filesShared !== undefined && { filesShared: row.filesShared }),
    }));
  }

  async loadArtifactContext(
    orgId: string,
    conversationId: string,
    runId?: string,
  ): Promise<RunTurn | null> {
    if (
      runId === undefined ||
      runId === '' ||
      !Types.ObjectId.isValid(orgId) ||
      !Types.ObjectId.isValid(conversationId)
    ) {
      return null;
    }
    const turn = await ChatSessionMessage.findOne({
      orgId,
      sessionId: conversationId,
      messageType: 'user_query',
      runId: { $eq: runId, $type: 'string' },
    })
      .select('authorUserId shareToolResults')
      .lean<TurnDoc>();
    if (!turn) {
      return null;
    }
    return {
      ...(turn.authorUserId && { authorUserId: turn.authorUserId.toString() }),
      ...(turn.shareToolResults !== undefined && {
        shareToolResults: turn.shareToolResults,
      }),
    };
  }
}
