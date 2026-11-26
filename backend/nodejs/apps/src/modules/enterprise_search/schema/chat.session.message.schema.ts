import mongoose, { Schema, Model } from 'mongoose';
import { CUT_SHORT_REASONS } from '../../agent-harness/contracts/generated/core/enums';
import { BUDGET_SCOPE_PATTERN } from '../../agent-harness/domain/cost-ledger';
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
    // G-15: the run that wrote the rated answer and the agent version it ran.
    runId: { type: String },
    agentKey: { type: String },
    agentVersion: { type: Number },
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
    // Set by erasure of another conversation that carried the same upload (QA-15G).
    removed: { type: Boolean },
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
// Lean reads that select `+label` (the history source, session forks) are untouched.
const withoutLabel = (
  _doc: unknown,
  ret: Record<string, unknown>,
): Record<string, unknown> => {
  delete ret['label'];
  return ret;
};

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
    // Set on a `bot_response` persisted from a cancelled/disconnected run
    // (see savePartialConversation) so the UI can render a "Stopped" marker.
    status: { type: String, enum: ['stopped'] },
    // A collaborator's turn (SS2): who sent it. Absent: the session's owner.
    senderId: { type: Schema.Types.ObjectId },
    // In a conversation continued privately (SS3): a stub standing for another participant's turn.
    foreignTurn: { type: Boolean },
    // Copied from another conversation by a fork (FORKFIX): its runs stayed there, so it names none.
    copied: { type: Boolean },
    // The durable run that wrote this answer (UX5): the activity panel finds the run after a reload.
    runId: { type: String },
    // An error answer's code (`messageType: 'error'`): the web app words it in the reader's language (ERR-UX).
    errorCode: { type: String },
    // With errorCode `mcp_tool_withdrawn` (WP-84): the labels of the servers that withdrew a tool, which the words name.
    errorServers: { type: [String], default: undefined },
    // With errorCode `operator_stopped` (ADR-OPS-01 D5): the control's scope and reason, never its note.
    errorStop: {
      type: new Schema({ scope: String, reason: String }, { _id: false }),
      default: undefined,
    },
    // An answer a limit cut short (BUDGETEND): which limit; the web app and the Slack bot show a notice.
    truncatedBy: { type: String, enum: [...CUT_SHORT_REASONS] },
    // Which shared budget ran out, with truncatedBy budget (C9.06, WP-51), as the extension that keeps it names it.
    budgetScope: { type: String, match: BUDGET_SCOPE_PATTERN },
    // WP-89: the run's settled outcome, which the web app words beside the answer's prose in the reader's language;
    // its `closing` is the English a surface with no UI (the Slack bot) appends.
    runOutcome: { type: Schema.Types.Mixed, default: undefined },
    // ANSWER-CONTRACT-FU-2: the request's subject was not found; the web app words it above the answer in the
    // reader's language, and leaves out its `line`, the English the content starts with for a surface with no UI.
    notFound: { type: Schema.Types.Mixed, default: undefined },
    // R32REPO-FU-1: what the run tried and could not do (a declined clone); worded the same way, its `line` left out.
    limits: { type: Schema.Types.Mixed, default: undefined },
    // A durable run's answer (P2.5a): the turn's confidentiality label. It names
    // records and live items, so it is never read unless asked for by name.
    label: { type: Schema.Types.Mixed, select: false },
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
      modelNote: { type: String },
    },
    appliedFilters: {
      apps: [
        {
          id: String,
          name: String,
          nodeType: String,
          connector: String,
          extension: String,
          mimeType: String,
          mentioned: Boolean,
          _id: false,
        },
      ],
      kb: [
        {
          id: String,
          name: String,
          nodeType: String,
          connector: String,
          extension: String,
          mimeType: String,
          mentioned: Boolean,
          _id: false,
        },
      ],
      recordGroups: [
        {
          id: String,
          name: String,
          nodeType: String,
          connector: String,
          extension: String,
          mimeType: String,
          mentioned: Boolean,
          _id: false,
        },
      ],
      records: [
        {
          id: String,
          name: String,
          nodeType: String,
          connector: String,
          extension: String,
          mimeType: String,
          mentioned: Boolean,
          _id: false,
        },
      ],
      recordsExact: [
        {
          id: String,
          name: String,
          nodeType: String,
          connector: String,
          extension: String,
          mimeType: String,
          mentioned: Boolean,
          _id: false,
        },
      ],
    },
    attachments: [attachmentRefSchema],
    // The /command a user_query was expanded from (SLASH1); `content` holds the expanded text, or only what was typed for `/review` (LC2FIX `storedQuestion`).
    command: {
      type: new Schema(
        {
          name: { type: String, required: true },
          version: { type: String, required: true },
          contentDigest: { type: String, required: true },
          args: { type: Schema.Types.Mixed, default: {} },
          typed: { type: String, required: true },
          owner: { type: String, required: true },
          authorId: { type: String },
          authorFormer: { type: Boolean },
          teamId: { type: String },
          files: { type: Number },
        },
        { _id: false, minimize: false },
      ),
      default: undefined,
    },
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
    // A message written in this request (insert, replace) carries its label in memory; it names records a reader may
    // not see, so it never leaves the server in a response (FG2: today's loop's answers have labels too).
    toJSON: { transform: withoutLabel },
    toObject: { transform: withoutLabel },
  },
);

// Serves both the seq-ordered read path and duplicate-seq protection —
// the real backstop for allocateSeq's optimistic $inc allocation.
chatSessionMessageSchema.index({ sessionId: 1, seq: 1 }, { unique: true });
// Erasure marks an erased upload in the org's other messages (QA-15G); only messages with attachments are indexed.
chatSessionMessageSchema.index(
  { orgId: 1, 'attachments.recordId': 1 },
  { partialFilterExpression: { 'attachments.recordId': { $exists: true } } },
);

export const ChatSessionMessage: Model<IChatSessionMessageDocument> =
  mongoose.model<IChatSessionMessageDocument>(
    'ChatSessionMessage',
    chatSessionMessageSchema,
  );
