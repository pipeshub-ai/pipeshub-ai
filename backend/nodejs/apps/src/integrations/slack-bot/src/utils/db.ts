import { connect as __connect, Schema, model, ConnectOptions } from 'mongoose';
import { MONGO_DB_NAME } from '../../../../libs/enums/db.enum';

import { config } from 'dotenv';

config();

export interface ConversationDocument {
  threadId: string;
  conversationId: string;
  botId: string;
  createdAt?: Date;
  updatedAt?: Date;
}

export const connect = async (
    url: string = process.env.MONGO_URI || '',
    opts: ConnectOptions = {},
  ): Promise<void> => {
    if (!url) {
      throw new Error('MONGO_URI environment variable is not set.');
    }
    try {
      await __connect(url, { dbName: MONGO_DB_NAME, ...opts });
      console.log('mongodb running'); // Consider using a proper logger
    } catch (err) {
      console.error('mongodb connection error:', err); // Consider using a proper logger
      throw err; // Re-throw the error to ensure the app fails to start if DB connection fails
    }
  };

const conversationSchema = new Schema<ConversationDocument>(
  {
    threadId: { type: String, required: true },
    conversationId: { type: String, required: true },
    botId: { type: String, required: true },
  },

  { timestamps: true },
);

conversationSchema.index({ threadId: 1, botId: 1 }, { unique: true });

export const Conversation = model<ConversationDocument>(
  'Conversation',
  conversationSchema,
  'slack_conversations',
);
