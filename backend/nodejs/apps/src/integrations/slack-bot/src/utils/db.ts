import { connect as __connect, Schema, model, ConnectOptions } from 'mongoose';

import { config } from 'dotenv';

config();

export interface ConversationDocument {
  threadId: string;

  conversationId: string;

  email: string;

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
      await __connect(url, opts);
      console.log('mongodb running'); // Consider using a proper logger
    } catch (err) {
      console.error('mongodb connection error:', err); // Consider using a proper logger
      throw err; // Re-throw the error to ensure the app fails to start if DB connection fails
    }
  };



const conversationSchema = new Schema<ConversationDocument>(
  {
    threadId: { type: String, required: true, unique: true },

    conversationId: { type: String, required: true },

    email: { type: String, required: true },
  },

  { timestamps: true },
);

export const Conversation = model<ConversationDocument>(
  'Conversation',
  conversationSchema,
);
