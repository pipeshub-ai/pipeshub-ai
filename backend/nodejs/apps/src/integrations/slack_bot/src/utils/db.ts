import { connect as __connect, Schema, model, ConnectOptions } from "mongoose";
import { config } from "dotenv";
config();

export interface ConversationDocument {
  threadId: string;
  conversationId: string;
  email: string;
  createdAt?: Date;
  updatedAt?: Date;
}

export const connect = async (url: string = process.env.MONGO_URI || '', opts: ConnectOptions = {}): Promise<void> => {
  return __connect(url, opts)
    .then(() => console.log("mongodb running"))
    .catch((err) => console.log(err));
};

const conversationSchema = new Schema<ConversationDocument>(
  {
    threadId: { type: String, required: true, unique: true },
    conversationId: { type: String, required: true },
    email: { type: String, required: true },
  },
  { timestamps: true }
);

export const Conversation = model<ConversationDocument>("Conversation", conversationSchema);
