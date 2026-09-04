import mongoose, { Schema, Model } from 'mongoose';
import {
  IChatSessionMessageDocument,
  IFeedback,
  IFollowUpQuestion,
  IMessageCitation,
} from '../types/conversation.interfaces';
import { CONFIDENCE_LEVELS, REASONING_EFFORT_VALUES } from '../constants/constants';

const toolCallItemSchema = new Schema(
  {
    toolName: { type: String, required: true },
    toolResult: { type: Schema.Types.Mixed },
  },
  { _id: false },
);

// Chain-of-thought turns (opt-out on the Python side — see reasoning_persistence.py).
const reasoningTurnSchema = new Schema(
  {
    messageId: { type: String },
    turnIndex: { type: Number },
    content: { type: String, required: true },
  },
  { _id: false },
);

const followUpQuestionSchema = new Schema<IFollowUpQuestion>(
  {
    question: { type: String, required: true },
    confidence: { type: String, enum: CONFIDENCE_LEVELS, required: true },
    reasoning: { type: String },
  },
  { _id: false },
);

const messageCitationSchema = new Schema<IMessageCitation>(
  {
    // Matches the actually-registered citation model name ('citation',
    // singular — see citation.schema.ts). The old embedded schema says
    // `ref: 'citations'` and every populate() call site compensates with an
    // explicit `model: 'citation'`; fixed here instead of carried forward.
    citationId: { type: Schema.Types.ObjectId, ref: 'citation' },
    relevanceScore: { type: Number, min: 0, max: 1 },
    excerpt: { type: String },
    context: { type: String },
  },
  { _id: false },
);

const feedbackSchema = new Schema<IFeedback>(
  {
    isHelpful: { type: Boolean },
    ratings: {
      accuracy: { type: Number, min: 1, max: 5 },
      relevance: { type: Number, min: 1, max: 5 },
      completeness: { type: Number, min: 1, max: 5 },
      clarity: { type: Number, min: 1, max: 5 },
    },
    categories: [
      {
        type: String,
        enum: [
          'incorrect_information',
          'missing_information',
          'irrelevant_information',
          'unclear_explanation',
          'poor_citations',
          'excellent_answer',
          'helpful_citations',
          'well_explained',
          'other',
        ],
      },
    ],
    comments: {
      positive: { type: String },
      negative: { type: String },
      suggestions: { type: String },
    },
    citationFeedback: [
      {
        citationId: { type: Schema.Types.ObjectId, ref: 'citation' },
        isRelevant: { type: Boolean },
        relevanceScore: { type: Number, min: 1, max: 5 },
        comment: { type: String },
      },
    ],
    followUpQuestionsHelpful: { type: Boolean },
    unusedFollowUpQuestions: [{ type: String }],
    source: {
      type: String,
      enum: ['user', 'system', 'admin', 'auto'],
      default: 'user',
    },
    feedbackProvider: { type: Schema.Types.ObjectId },
    timestamp: { type: Number, default: Date.now },
    revisions: [
      {
        updatedFields: [{ type: String }],
        previousValues: { type: Map, of: Schema.Types.Mixed },
        updatedBy: { type: Schema.Types.ObjectId },
        updatedAt: { type: Number, default: Date.now },
      },
    ],
    metrics: {
      timeToFeedback: { type: Number },
      userInteractionTime: { type: Number },
      feedbackSessionId: { type: String },
      userAgent: { type: String },
      platform: { type: String },
    },
  },
  { _id: false },
);

// Schema for reference data items (IDs for follow-up queries)
const referenceDataItemSchema = new Schema(
  {
    name: { type: String, required: false }, // Display name
    id: { type: String, required: false }, // Technical ID (numeric ID, UUID, etc.) - Optional
    type: { type: String }, // Item type (e.g., "project", "issue", "file", "notebook", "page")
    app: { type: String }, // Application name (jira, confluence, sharepoint, slack, etc.)
    webUrl: { type: String }, // Web URL to open the item in the browser
    metadata: { type: Map, of: String }, // App-specific fields (e.g. key for Jira, siteId for SharePoint)
  },
  { _id: false },
);

const attachmentRefSchema = new Schema(
  {
    recordId: { type: String, required: true },
    recordName: { type: String },
    mimeType: { type: String },
    extension: { type: String },
    virtualRecordId: { type: String },
    // Origin metadata ('upload' | 'paste-text') — see IChatAttachmentRef.source.
    source: { type: String, enum: ['upload', 'paste-text'] },
  },
  { _id: false },
);

/**
 * One message row. Formerly an embedded subdocument of `conversations` /
 * `agentConversations`; now its own collection ordered by `seq` (see
 * allocateSeq in utils.ts) rather than array position, so a session can
 * exceed MongoDB's 16MB document limit and large threads don't have to be
 * loaded/rewritten in full on every turn.
 *
 * `sessionId` / `orgId` / `seq` are internal addressing fields, not part of
 * any documented response shape — attachMessages() strips them before a
 * message ever reaches a response.
 */
const chatSessionMessageSchema = new Schema<IChatSessionMessageDocument>(
  {
    sessionId: {
      type: Schema.Types.ObjectId,
      required: true,
      ref: 'ChatSession',
      index: true,
    },
    orgId: { type: Schema.Types.ObjectId, required: true, index: true },
    // Per-session monotonic sort key allocated from the parent session's
    // `nextSeq` counter. Gaps are legal; never treat this as a count or index.
    seq: { type: Number, required: true },

    messageType: {
      type: String,
      enum: [
        'user_query',
        'bot_response',
        'error',
        'feedback',
        'system',
        'tool_call',
      ],
      required: true,
    },
    content: { type: String, default: '' },
    contentFormat: {
      type: String,
      enum: ['MARKDOWN', 'JSON', 'HTML'],
      default: 'MARKDOWN',
    },
    citations: [messageCitationSchema],
    confidence: { type: String, enum: CONFIDENCE_LEVELS },
    followUpQuestions: [followUpQuestionSchema],
    feedback: [feedbackSchema],
    metadata: {
      processingTimeMs: { type: Number },
      modelVersion: { type: String },
      aiTransactionId: { type: String },
    },
    modelInfo: {
      modelKey: { type: String },
      modelName: { type: String },
      modelProvider: { type: String },
      chatMode: { type: String, default: 'quick' },
      modelFriendlyName: { type: String },
      reasoningEffort: { type: String, enum: REASONING_EFFORT_VALUES },
    },
    appliedFilters: {
      apps: [
        {
          id: String,
          name: String,
          nodeType: String,
          connector: String,
          _id: false,
        },
      ],
      kb: [
        {
          id: String,
          name: String,
          nodeType: String,
          connector: String,
          _id: false,
        },
      ],
    },
    attachments: [attachmentRefSchema],
    // Reference data for follow-up queries (stores IDs from tool responses)
    referenceData: [referenceDataItemSchema],
    // Tool call data for tool_call messageType
    tools: [toolCallItemSchema],
    // Persisted chain-of-thought (additive, opt-out — see reasoningTurnSchema).
    reasoning: [reasoningTurnSchema],
    // Ordered agent-activity transcript (additive, `agui` protocol only) --
    // Mixed for the same reason as the legacy schema's identical field:
    // shape varies by `type`, and `sub_agent` nests the same shape recursively.
    parts: [Schema.Types.Mixed],
  },
  {
    timestamps: true, // parity with the old embedded messageSchema
    versionKey: false, // embedded subdocuments never had __v; a top-level one would be a new, leaking field
    collection: 'chatSessionMessages',
  },
);

// Serves both the seq-ordered read path and duplicate-seq protection —
// the real backstop for allocateSeq's optimistic $inc allocation.
chatSessionMessageSchema.index({ sessionId: 1, seq: 1 }, { unique: true });

export const ChatSessionMessage: Model<IChatSessionMessageDocument> =
  mongoose.model<IChatSessionMessageDocument>(
    'ChatSessionMessage',
    chatSessionMessageSchema,
  );
