import { Document, Types, Model } from 'mongoose';
import { ConfidenceLevel } from '../constants/constants';
import { ICitation } from '../schema/citation.schema';

export interface IFollowUpQuestion {
  question: string;
  confidence: string;
  reasoning?: string;
}

export interface IMessageCitation {
  citationId?: Types.ObjectId;
  relevanceScore?: number;
  excerpt?: string;
  context?: string;
}

export interface IFeedback {
  isHelpful?: boolean;
  ratings?: {
    accuracy?: number;
    relevance?: number;
    completeness?: number;
    clarity?: number;
  };
  categories?: string[];
  comments?: {
    positive?: string;
    negative?: string;
    suggestions?: string;
  };
  citationFeedback?: Array<{
    citationId?: Types.ObjectId;
    isRelevant?: boolean;
    relevanceScore?: number;
    comment?: string;
  }>;
  followUpQuestionsHelpful?: boolean;
  unusedFollowUpQuestions?: string[];
  source?: 'user' | 'system' | 'admin' | 'auto';
  feedbackProvider?: Types.ObjectId;
  timestamp?: Date;
  revisions?: Array<{
    updatedFields?: string[];
    previousValues?: Map<string, any>;
    updatedBy?: Types.ObjectId;
    updatedAt?: Number;
  }>;
  metrics?: {
    timeToFeedback?: number;
    userInteractionTime?: number;
    feedbackSessionId?: string;
    userAgent?: string;
    platform?: string;
  };
}

interface IMessageMetadata {
  processingTimeMs?: number;
  modelVersion?: string;
  aiTransactionId?: string;
  reason?: string;
}

// Reference data item for follow-up queries (stores IDs that were in the response)
export interface IReferenceDataItem {
  name?: string;        // Display name shown to user
  id?: string;         // Technical ID (numeric ID, UUID, etc.)
  type?: string;       // Item type (e.g., "project", "issue", "file", "notebook", "page")
  app?: string;        // Application name (jira, confluence, sharepoint, slack, drive, gmail, etc.)
  webUrl?: string;     // Web URL to open the item in the browser
  /** App-specific fields (e.g. key for Jira, siteId for SharePoint) — extend without schema churn */
  metadata?: Record<string, string>;
}

export interface IAppliedFilterNode {
  id: string;
  name: string;
  nodeType: string;
  connector: string;
}

export interface IChatAttachmentRef {
  recordId: string;
  recordName?: string;
  mimeType?: string;
  extension?: string;
  virtualRecordId?: string;
}
export interface IToolCallItem {
  toolName: string;
  toolResult: any;
}

/**
 * One model-turn's chain-of-thought, as accumulated by the Python
 * `TerminalAnswerStreamer` and attached to `completion_data.reasoning`
 * (see `reasoning_persistence.py` — additive, gated behind
 * `PIPESHUB_PERSIST_REASONING`, absent entirely when disabled).
 */
export interface IReasoningTurn {
  messageId?: string;
  turnIndex?: number;
  content: string;
}

/**
 * One entry in the ordered agent-activity transcript — text, reasoning,
 * a tool call, or a nested sub-agent's own timeline — assembled by the
 * Python `TranscriptCollector` (`protocol/transcript_collector.py`) and
 * attached to `completion_data.parts`. Mirrors that module's `MessagePart`
 * TypedDict byte-for-byte; kept as one loosely-typed interface (not a
 * discriminated union) since Mongoose persists this as `Schema.Types.Mixed`
 * and every field beyond `type` is optional depending on the part kind.
 *
 * Only ever carries a bounded preview of any external tool result (see
 * that module's docstring) — never the full tool payload.
 */
export interface IMessagePart {
  type: 'text' | 'reasoning' | 'tool_call' | 'sub_agent';
  content?: string;
  toolCallId?: string;
  toolName?: string;
  args?: string;
  /** Human-readable summary of `args`, computed server-side (see PipesHubToolSummarizer). */
  argsSummary?: string;
  status?: 'running' | 'completed' | 'failed' | 'blocked';
  resultPreview?: string;
  /** Human-readable summary of the tool result, computed server-side from the full (untruncated) output. */
  resultSummary?: string;
  /** Blob-backed artifact ID for the full tool result (recoverable on follow-up turns). */
  artifactId?: string;
  runId?: string;
  roleName?: string;
  parts?: IMessagePart[];
}

export interface IMessage {
  messageType: 'user_query' | 'bot_response' | 'error' | 'feedback' | 'system' | 'tool_call';
  content: string;
  contentFormat?: 'MARKDOWN' | 'JSON' | 'HTML';
  citations?: IMessageCitation[];
  confidence?: string;
  followUpQuestions?: IFollowUpQuestion[];
  feedback?: IFeedback[];
  metadata?: IMessageMetadata;
  createdAt?: Date;
  updatedAt?: Date;
  modelInfo?: IAIModel;
  appliedFilters?: {
    apps?: IAppliedFilterNode[];
    kb?: IAppliedFilterNode[];
  };
  attachments?: IChatAttachmentRef[];
  // Reference data for follow-up queries (IDs from tool responses)
  referenceData?: IReferenceDataItem[];
  // Tool call data for tool_call messageType
  tools?: IToolCallItem[];
  /** Persisted chain-of-thought for this bot_response turn (opt-in, see IReasoningTurn). */
  reasoning?: IReasoningTurn[];
  /** Ordered agent-activity transcript for this bot_response turn — see IMessagePart. */
  parts?: IMessagePart[];
}

export interface IConversation {
  userId: Types.ObjectId;
  orgId: Types.ObjectId;
  title?: string;
  initiator: Types.ObjectId;
  messages: IMessageDocument[];
  isShared?: boolean;
  shareLink?: string;
  sharedWith?: Array<{
    userId: Types.ObjectId;
    accessLevel: 'read' | 'write';
  }>;
  isDeleted?: boolean;
  deletedBy?: Types.ObjectId;
  isArchived?: boolean;
  archivedBy?: Types.ObjectId;
  lastActivityAt?: Number;
  tags?: Types.ObjectId[];
  conversationSource:
  | 'enterprise_search'
  | 'records'
  | 'connectors'
  | 'internet_search'
  | 'personal_kb_search'
  | 'agent';
  conversationSourceRecordId?: Types.ObjectId;
  conversationSourceConnectorIds?: Types.ObjectId[];
  conversationSourceRecordType?: string;
  createdAt?: Date;
  updatedAt?: Date;
  failReason?: String;
  status?: String;
  // Model information used for this conversation
  modelInfo?: IAIModel;
  // Errors array to track errors during conversation
  conversationErrors?: Array<{
    message: string;
    errorType?: string;
    timestamp?: Date;
    messageId?: Types.ObjectId;
    stack?: string;
    metadata?: Map<string, any>;
  }>;
  // Additional metadata for useful information
  metadata?: Map<string, any>;
}

export interface IAgentConversation extends IConversation {
  agentKey: string;
  compactedSummary?: string;
  compactedAtTurnIndex?: number;
  compactedAtTimestamp?: number;
}

export interface IMessageDocument extends Document, IMessage {
  // Document methods are inherited
}

export interface IConversationDocument extends Document, IConversation {
  // Document methods are inherited
}

export interface IConversationModel extends Model<IConversationDocument> {
  // Static methods go here
}

export interface AIServiceResponse<T> {
  statusCode: number;
  data?: T;
  msg?: string;
}

export type AnswerMatchType = 'Exact Match' | 'Partial Match' | 'No Match';

export interface IAIResponse {
  answer: string;
  citations: ICitation[];
  confidence: ConfidenceLevel;
  reason: string;
  answerMatchType: AnswerMatchType;
  documentIndexes: string[];
  followUpQuestions?: IFollowUpQuestion[];
  feedback?: IFeedback[];
  metadata?: {
    processingTimeMs?: number;
    modelVersion?: string;
    aiTransactionId?: string;
    reason?: string;
  };
  modelInfo?: IAIModel;
  // Reference data for follow-up queries (IDs from tool responses)
  referenceData?: IReferenceDataItem[];
  /** Present only when `PIPESHUB_PERSIST_REASONING=true` on the Python side. */
  reasoning?: IReasoningTurn[];
  /** Ordered agent-activity transcript (`agui` protocol only) — see IMessagePart. */
  parts?: IMessagePart[];
}

export interface IAIModel {
  modelKey: string;
  modelName: string;
  modelProvider: string;
  chatMode: string;
  modelFriendlyName?: string;
}