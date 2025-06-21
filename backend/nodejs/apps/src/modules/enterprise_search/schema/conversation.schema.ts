import mongoose, { Schema, Model } from 'mongoose';
import {
  IConversation,
  IMessage,
  IFeedback,
  IMessageCitation,
  IFollowUpQuestion,
} from '../types/conversation.interfaces';
import { CONFIDENCE_LEVELS } from '../constants/constants';

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
    citationId: { type: Schema.Types.ObjectId, ref: 'citations' },
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
        citationId: { type: Schema.Types.ObjectId, ref: 'citations' },
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

const messageSchema = new Schema<IMessage>(
  {
    messageType: {
      type: String,
      enum: ['user_query', 'bot_response', 'error', 'feedback', 'system'],
      required: true,
    },
    content: { type: String, required: true },
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
  },
  { timestamps: true },
);

// Schema for the overall conversation/thread
const conversationSchema = new Schema<IConversation>(
  {
    userId: { type: Schema.Types.ObjectId, required: true, index: true },
    orgId: { type: Schema.Types.ObjectId, required: true, index: true },
    title: { type: String },
    initiator: { type: Schema.Types.ObjectId, required: true, index: true },
    messages: [messageSchema],
    isShared: { type: Boolean, default: false },
    shareLink: { type: String },
    sharedWith: [
      {
        userId: { type: Schema.Types.ObjectId },
        accessLevel: { type: String, enum: ['read', 'write'], default: 'read' },
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
  },
  { timestamps: true },
);

// Create additional indexes as needed
conversationSchema.index({ orgId: 1, initiator: 1 });
conversationSchema.index({ isShared: 1 });
conversationSchema.index({ 'messages.content': 'text' });

// Export the model
export const Conversation: Model<IConversation> = mongoose.model<IConversation>(
  'conversations',
  conversationSchema,
);
