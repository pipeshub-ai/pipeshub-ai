import { Schema, model } from 'mongoose';

export interface SlackThreadDocument {
  threadId: string;
  conversationId: string;
  botId: string;
  email: string;
  createdAt?: Date;
  updatedAt?: Date;
}

const slackThreadSchema = new Schema<SlackThreadDocument>(
  {
    threadId: { type: String, required: true },
    conversationId: { type: String, required: true },
    botId: { type: String, required: true },
    email: { type: String, required: true },
  },

  { timestamps: true },
);

export const SlackThread = model<SlackThreadDocument>(
  'Conversation',
  slackThreadSchema,
  'slack_conversations',
);
