import mongoose, { Schema, Model } from 'mongoose';
import { IChatSessionDocument } from '../types/conversation.interfaces';
import { RESPOND_MODES } from '../services/collaboration/mentions/mention.types';
import {
  CHAT_SCHEMA_VERSION,
  COLLABORATOR_ACCESS_LEVELS,
  COLLABORATOR_PRINCIPAL_TYPES,
  OWNERSHIP_HISTORY_MAX,
  REASONING_EFFORT_VALUES,
  SHARED_WITH_MAX,
} from '../constants/constants';

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
// `principalType` and `addedBy` are deliberately not `required`: the existing
// share writer persists `{userId, accessLevel}` under `runValidators`, and
// legacy rows have neither. Readers must key on `userId`/`teamId` presence,
// never on `principalType` (74 §1: defaults are invisible to the query engine).
const collaboratorSchema = new Schema(
  {
    principalType: { type: String, enum: COLLABORATOR_PRINCIPAL_TYPES },
    userId: { type: Schema.Types.ObjectId },
    teamId: { type: String },
    accessLevel: {
      type: String,
      enum: COLLABORATOR_ACCESS_LEVELS,
      required: true,
      default: 'read',
    },
    addedBy: { type: Schema.Types.ObjectId },
    addedAt: { type: Date, default: Date.now },
    updatedAt: { type: Date },
  },
  { _id: false },
);

const activeRunSchema = new Schema(
  {
    runId: { type: String, required: true },
    userId: { type: Schema.Types.ObjectId, required: true },
    instanceId: { type: String },
    startedAt: { type: Date, required: true },
    leaseExpiresAt: { type: Date, required: true },
  },
  { _id: false },
);

const ownershipTransferSchema = new Schema(
  {
    fromUserId: { type: Schema.Types.ObjectId, required: true },
    toUserId: { type: Schema.Types.ObjectId, required: true },
    at: { type: Date, required: true },
  },
  { _id: false },
);

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
    sharedWith: {
      type: [collaboratorSchema],
      default: [],
      validate: {
        validator: (rows: unknown[]) => rows.length <= SHARED_WITH_MAX,
        message: `sharedWith exceeds ${String(SHARED_WITH_MAX)} entries`,
      },
    },
    settings: {
      editorsCanInvite: { type: Boolean, default: false },
      ownerContentShared: { type: Boolean, default: false },
      // No default: absent reads as `smart`, and a new key on every row would change flag-off documents.
      respondMode: { type: String, enum: RESPOND_MODES },
    },
    // Per-user archive and Leave state; kept off every read (74 §1, §4).
    archivedFor: { type: [Schema.Types.ObjectId], default: [], select: false },
    hiddenFor: { type: [Schema.Types.ObjectId], default: [], select: false },
    activeRun: { type: activeRunSchema, default: null },
    rev: { type: Number, default: 0 },
    aclVersion: { type: Number, default: 0 },
    creationKey: { type: String, select: false },
    ownershipHistory: {
      type: [ownershipTransferSchema],
      default: [],
      validate: {
        validator: (rows: unknown[]) => rows.length <= OWNERSHIP_HISTORY_MAX,
        message: `ownershipHistory exceeds ${String(OWNERSHIP_HISTORY_MAX)} entries`,
      },
    },
    schemaVersion: {
      type: Number,
      default: CHAT_SCHEMA_VERSION,
      select: false,
    },
    isDeleted: { type: Boolean, default: false },
    deletedBy: { type: Schema.Types.ObjectId },
    isArchived: { type: Boolean, default: false },
    archivedBy: { type: Schema.Types.ObjectId },
    lastActivityAt: { type: Number, default: Date.now },
    status: {
      type: String,
      enum: ['None', 'Inprogress', 'Complete', 'Failed', 'Stopped'],
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

    // ---- Project linking (optional on both chat and agent sessions) ----
    /** Reference to `projects` collection. Absent = not linked to a project. */
    projectId: { type: Schema.Types.ObjectId, index: true },
    /**
     * Per-conversation override of the owning project's `chatSharing`
     * default. 'project' exposes this chat to every project member with at
     * least viewer access; 'private' (default) keeps it visible only to its
     * owner even inside a shared project. See ProjectService.computeRole /
     * getProjectConversations for the read-side of this rule.
     */
    projectVisibility: { type: String, enum: ['private', 'project'] },
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
// One array path per index (multikey rule); equality fields before the sort key.
chatSessionSchema.index({
  orgId: 1,
  'sharedWith.userId': 1,
  sessionType: 1,
  isDeleted: 1,
  lastActivityAt: -1,
});
chatSessionSchema.index({
  orgId: 1,
  'sharedWith.teamId': 1,
  sessionType: 1,
  isDeleted: 1,
  lastActivityAt: -1,
});
chatSessionSchema.index(
  { orgId: 1, initiator: 1, creationKey: 1 },
  {
    unique: true,
    partialFilterExpression: { creationKey: { $type: 'string' } },
  },
);
chatSessionSchema.index({ projectId: 1, orgId: 1, isDeleted: 1, lastActivityAt: -1 });
chatSessionSchema.index({ lastActivityAt: -1 });

export const ChatSession: Model<IChatSessionDocument> =
  mongoose.model<IChatSessionDocument>('ChatSession', chatSessionSchema);
