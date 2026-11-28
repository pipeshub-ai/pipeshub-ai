import mongoose, { Schema, type Model } from 'mongoose';

/**
 * The reader check's closure of one answer version, by `rowId@ver`: what a reader must pass to see that version as an
 * earlier answer of another. Made from the version's label and the closures of the versions it was given, never
 * returned to a client, and kept after the version's own label leaves the row's archive (what was built on the
 * version is still judged by it).
 */
export interface IChatAnswerClosure {
  _id: string;
  orgId: mongoose.Types.ObjectId;
  sessionId?: mongoose.Types.ObjectId;
  rowId: string;
  ver: string;
  rev: number;
  self: unknown;
  add: unknown;
  chain: unknown;
  folded: unknown;
  size: number;
  spans: unknown;
  pub: unknown;
  shares: unknown;
  digest: string;
  at: Date;
}

const chatAnswerClosureSchema = new Schema<IChatAnswerClosure>(
  {
    _id: { type: String, required: true },
    orgId: { type: Schema.Types.ObjectId, required: true },
    sessionId: { type: Schema.Types.ObjectId },
    rowId: { type: String, required: true },
    ver: { type: String, required: true },
    rev: { type: Number, required: true },
    self: { type: Schema.Types.Mixed },
    add: { type: Schema.Types.Mixed },
    chain: { type: Schema.Types.Mixed },
    folded: { type: Schema.Types.Mixed },
    // Read without building the closure (its weight, and the chats whose shares it is checked against): a write that
    // dropped them would leave every kept closure unreadable.
    size: { type: Number, required: true },
    spans: { type: Schema.Types.Mixed },
    pub: { type: Schema.Types.Mixed },
    shares: { type: Schema.Types.Mixed },
    digest: { type: String, required: true },
    at: { type: Date, required: true },
  },
  {
    versionKey: false,
    collection: 'chatAnswerClosures',
    // Mixed fields hold folds whose keys are ids: an empty object is a value, not nothing.
    minimize: false,
  },
);

// The closures of one chat's answers, for removing them with the chat.
chatAnswerClosureSchema.index({ orgId: 1, sessionId: 1 });

export const ChatAnswerClosure: Model<IChatAnswerClosure> =
  mongoose.model<IChatAnswerClosure>(
    'ChatAnswerClosure',
    chatAnswerClosureSchema,
  );
