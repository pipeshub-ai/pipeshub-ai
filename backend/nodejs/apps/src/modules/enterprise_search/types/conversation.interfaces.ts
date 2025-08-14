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

export interface IMessage {
  messageType: 'user_query' | 'bot_response' | 'error' | 'feedback' | 'system';
  content: string;
  contentFormat?: 'MARKDOWN' | 'JSON' | 'HTML';
  citations?: IMessageCitation[];
  confidence?: string;
  followUpQuestions?: IFollowUpQuestion[];
  feedback?: IFeedback[];
  metadata?: IMessageMetadata;
  createdAt?: Date;
  updatedAt?: Date;
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
}

export interface IAgentConversation extends IConversation {
  agentKey: string;
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
}
