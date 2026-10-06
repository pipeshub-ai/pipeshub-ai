import mongoose, { Schema, Model, Document, Types } from 'mongoose';

export interface IChatSessionReadState {
  orgId: Types.ObjectId;
  userId: Types.ObjectId;
  sessionId: Types.ObjectId;
  /** Highest message `seq` the user has seen; -1 = nothing read. Written with `$max`. */
  lastReadSeq: number;
  schemaVersion?: number;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface IChatSessionReadStateDocument
  extends Document,
    IChatSessionReadState {}

// Separate collection so per-view writes do not contend with the session's
// lease heartbeat and `lastActivityAt` (74 §4).
const chatSessionReadStateSchema = new Schema<IChatSessionReadStateDocument>(
  {
    orgId: { type: Schema.Types.ObjectId, required: true },
    userId: { type: Schema.Types.ObjectId, required: true },
    sessionId: { type: Schema.Types.ObjectId, required: true },
    lastReadSeq: { type: Number, required: true, default: -1 },
    schemaVersion: { type: Number, default: 1 },
  },
  { timestamps: true, versionKey: false, collection: 'chatSessionReadStates' },
);

chatSessionReadStateSchema.index({ userId: 1, sessionId: 1 }, { unique: true });

export const ChatSessionReadState: Model<IChatSessionReadStateDocument> =
  mongoose.model<IChatSessionReadStateDocument>(
    'ChatSessionReadState',
    chatSessionReadStateSchema,
  );
