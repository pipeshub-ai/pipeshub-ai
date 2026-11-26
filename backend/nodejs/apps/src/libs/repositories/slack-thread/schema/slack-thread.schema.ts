import { Schema, model } from 'mongoose';

export interface SlackThreadDocument {
  threadId: string;
  conversationId: string;
  botId: string;
  /** The link's organisation, for a repository that serves several (`installSlackBotEdition`); unset here. */
  orgId?: string;
  email: string;
  createdAt?: Date;
  updatedAt?: Date;
}

const slackThreadSchema = new Schema<SlackThreadDocument>(
  {
    threadId: { type: String, required: true },
    conversationId: { type: String, required: true },
    botId: { type: String, required: true },
    orgId: { type: String },
    email: { type: String, required: true },
  },

  { timestamps: true },
);

export const SlackThread = model<SlackThreadDocument>(
  'Conversation',
  slackThreadSchema,
  'slack_conversations',
);
