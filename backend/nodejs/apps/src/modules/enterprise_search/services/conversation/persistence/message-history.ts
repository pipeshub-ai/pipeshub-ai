import { ClientSession, Types } from 'mongoose';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import { IMessage } from '../../../types/conversation.interfaces';

export interface IConversationHistory {
  /** Rows with `seq` below `seq`, oldest first: the history a turn is answered from. */
  historyBefore(
    sessionId: Types.ObjectId | string,
    seq: number,
    opts?: { dbSession?: ClientSession | null },
  ): Promise<IMessage[]>;
}

export class MongoConversationHistory implements IConversationHistory {
  async historyBefore(
    sessionId: Types.ObjectId | string,
    seq: number,
    opts: { dbSession?: ClientSession | null } = {},
  ): Promise<IMessage[]> {
    let query = ChatSessionMessage.find({ sessionId, seq: { $lt: seq } }).sort({
      seq: 1,
    });
    if (opts.dbSession) {
      query = query.session(opts.dbSession);
    }
    return (await query.lean().exec()) as unknown as IMessage[];
  }
}
