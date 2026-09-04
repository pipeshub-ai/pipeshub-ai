import mongoose, { Schema, Model } from 'mongoose';
import { IChatSessionDocument } from '../types/conversation.interfaces';
import { REASONING_EFFORT_VALUES } from '../constants/constants';

/**
 * Single collection backing both plain chat and agent chat threads
 * (`sessionType` discriminates). Replaces the separate `conversations` and
 * `agentConversations` collections. `messages` lives in `ChatSessionMessage`
 * (see chat.session.message.schema.ts) — not embedded here, to stay clear of
 * MongoDB's 16MB document limit and keep session reads/writes small.
 *
 * `sessionType` and `nextSeq` are internal bookkeeping fields, not part of
 * any documented API response shape, so they are `select: false` — this is
 * the structural guard against leaking them into a response even from a
 * `.lean()` query that forgets to project them out explicitly.
 */
const chatSessionSchema = new Schema<IChatSessionDocument>(
  {
    sessionType: {
      type: String,
      enum: ['chat', 'agent'],
      required: true,
      default: 'chat',
      select: false,
    },
    // Monotonic counter used solely to allocate message `seq` values; never
    // read as a message count (messages can be soft/hard-removed leaving gaps).
    nextSeq: { type: Number, default: 0, select: false },

    userId: { type: Schema.Types.ObjectId, required: true, index: true },
    orgId: { type: Schema.Types.ObjectId, required: true, index: true },
    title: { type: String },
    initiator: { type: Schema.Types.ObjectId, required: true, index: true },
    isShared: { type: Boolean, default: false },
    shareLink: { type: String },
    sharedWith: [
      {
        userId: { type: Schema.Types.ObjectId },
        accessLevel: {
          type: String,
          enum: ['read', 'write'],
          default: 'read',
        },
      },
      { _id: false },
    ],
    isDeleted: { type: Boolean, default: false },
    deletedBy: { type: Schema.Types.ObjectId },
    isArchived: { type: Boolean, default: false },
    archivedBy: { type: Schema.Types.ObjectId },
    lastActivityAt: { type: Number, default: Date.now },
    status: {
      type: String,
      enum: ['None', 'Inprogress', 'Complete', 'Failed'],
    },
    failReason: { type: String },
    // Model information used for this session
    modelInfo: {
      modelKey: { type: String },
      modelName: { type: String },
      modelProvider: { type: String },
      chatMode: { type: String, default: 'quick' },
      modelFriendlyName: { type: String },
      reasoningEffort: { type: String, enum: REASONING_EFFORT_VALUES },
    },
    // Errors array to track errors during the session
    conversationErrors: [
      {
        message: { type: String, required: true },
        errorType: { type: String },
        timestamp: { type: Date, default: Date.now },
        messageId: { type: Schema.Types.ObjectId },
        stack: { type: String },
        metadata: { type: Map, of: Schema.Types.Mixed },
      },
    ],
    // Additional metadata for useful information
    metadata: {
      type: Map,
      of: Schema.Types.Mixed,
    },

    // ---- Agent-only fields (undefined when sessionType === 'chat') ----
    agentKey: { type: String, index: true }, // Reference to agent _key in ArangoDB
    conversationSource: {
      type: String,
      enum: ['agent_chat'],
    },
    // Context compaction: deterministic summary of older turns, populated
    // lazily by a background job or on session load when turn count
    // exceeds a threshold.
    compactedSummary: { type: String },
    compactedAtTurnIndex: { type: Number },
    compactedAtTimestamp: { type: Number },
  },
  { timestamps: true, collection: 'chatSessions' },
);

// Create additional indexes as needed
chatSessionSchema.index({
  sessionType: 1,
  orgId: 1,
  userId: 1,
  lastActivityAt: -1,
});
chatSessionSchema.index({ sessionType: 1, orgId: 1, initiator: 1 });
chatSessionSchema.index({ agentKey: 1, orgId: 1 });
chatSessionSchema.index({ userId: 1, agentKey: 1 });
chatSessionSchema.index({ isShared: 1 });
chatSessionSchema.index({ lastActivityAt: -1 });

export const ChatSession: Model<IChatSessionDocument> =
  mongoose.model<IChatSessionDocument>('ChatSession', chatSessionSchema);
