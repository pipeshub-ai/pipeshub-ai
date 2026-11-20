import { ClientSession, Types } from 'mongoose';
import {
  ChatSessionMessage,
  NOT_REMOVED,
} from '../../../schema/chat.session.message.schema';
import { IMessage } from '../../../types/conversation.interfaces';

export interface IConversationHistory {
  /**
   * Rows with `seq` below `seq`, oldest first: the history a turn is answered from. `withLabels` also reads each
   * row's `label`, which names records a reader may not see: for the history's label only, never for a response.
   */
  historyBefore(
    sessionId: Types.ObjectId | string,
    seq: number,
    opts?: { dbSession?: ClientSession | null; withLabels?: boolean },
  ): Promise<IMessage[]>;
}

export class MongoConversationHistory implements IConversationHistory {
  async historyBefore(
    sessionId: Types.ObjectId | string,
    seq: number,
    opts: { dbSession?: ClientSession | null; withLabels?: boolean } = {},
  ): Promise<IMessage[]> {
    let query = ChatSessionMessage.find({
      sessionId,
      seq: { $lt: seq },
      ...NOT_REMOVED,
    }).sort({ seq: 1 });
    if (opts.withLabels) {
      query = query.select('+label') as typeof query;
    }
    if (opts.dbSession) {
      query = query.session(opts.dbSession);
    }
    return await query.lean().exec();
  }
}
