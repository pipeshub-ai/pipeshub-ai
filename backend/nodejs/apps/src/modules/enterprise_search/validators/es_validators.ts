// es_schema.ts
import { z } from 'zod';

// Regular expression for MongoDB ObjectId validation
const objectIdRegex = /^[0-9a-fA-F]{24}$/;

export const enterpriseSearchCreateSchema = z.object({
  body: z.object({
    query: z
      .string({ required_error: 'Query is required' })
      .min(1, { message: 'Query is required' })
      .max(100000, {
        message: 'Query exceeds maximum length of 100000 characters',
      }),
    recordIds: z
      .array(
        z
          .string()
          .regex(objectIdRegex, { message: 'Invalid record ID format' }),
      )
      .optional(),
    departments: z
      .array(
        z
          .string()
          .regex(objectIdRegex, { message: 'Invalid department ID format' }),
      )
      .optional(),
    filters: z
      .object({
        apps: z.array(z.string()).optional(),
        kb: z.array(z.string()).optional(),
      })
      .optional(),
    modelKey: z
      .string()
      .min(1, { message: 'Model key is required' })
      .optional(),
    modelName: z
      .string()
      .min(1, { message: 'Model name is required' })
      .optional(),
    chatMode: z
      .string()
      .min(1, { message: 'Chat mode is required' })
      .optional(),
    modelFriendlyName: z
      .string()
      .min(1, { message: 'Model friendly name is required' })
      .optional(),
  }),
});

export const conversationIdParamsSchema = z.object({
  params: z.object({
    conversationId: z
      .string()
      .regex(objectIdRegex, { message: 'Invalid message ID format' }),
  }),
});

export const conversationTitleParamsSchema = conversationIdParamsSchema.extend({
  body: z.object({
    title: z
      .string()
      .min(1, { message: 'Title is required' })
      .max(200, { message: 'Title must be less than 200 characters' }),
  }),
});

export const conversationShareParamsSchema = conversationIdParamsSchema.extend({
  body: z.object({
    userIds: z
      .array(z.string().regex(objectIdRegex))
      .min(1, { message: 'At least one user ID is required' }),
  }),
});

export const addMessageParamsSchema = enterpriseSearchCreateSchema.extend({
  params: z.object({
    conversationId: z.string().regex(objectIdRegex, {
      message: 'Invalid conversation ID format',
    }),
  }),
  body: z.object({
    query: z.string().min(1, { message: 'Query is required' }),
    filters: z
      .object({
        apps: z.array(z.string()).optional(),
        kb: z.array(z.string()).optional(),
      })
      .optional(),
    modelKey: z
      .string()
      .min(1, { message: 'Model key is required' })
      .optional(),
    modelName: z
      .string()
      .min(1, { message: 'Model name is required' })
      .optional(),
    chatMode: z
      .string()
      .min(1, { message: 'Chat mode is required' })
      .optional(),
    modelFriendlyName: z
      .string()
      .min(1, { message: 'Model friendly name is required' })
      .optional(),
  }),
});

export const messageIdParamsSchema = z.object({
  params: z.object({
    messageId: z
      .string()
      .regex(objectIdRegex, { message: 'Invalid message ID format' }),
  }),
});

export const regenerateAnswersParamsSchema = z.object({
  params: z.object({
    conversationId: z
      .string()
      .regex(objectIdRegex, { message: 'Invalid message ID format' }),
    messageId: z
      .string()
      .regex(objectIdRegex, { message: 'Invalid message ID format' }),
  }),
  body: z.object({
    filters: z
      .object({
        apps: z.array(z.string()).optional(),
        kb: z.array(z.string()).optional(),
      })
      .optional(),
    modelKey: z
      .string()
      .min(1, { message: 'Model key is required' })
      .optional(),
    modelName: z
      .string()
      .min(1, { message: 'Model name is required' })
      .optional(),
    chatMode: z
      .string()
      .min(1, { message: 'Chat mode is required' })
      .optional(),
    modelFriendlyName: z
      .string()
      .min(1, { message: 'Model friendly name is required' })
      .optional(),
  }),
});

export const regenerateAgentAnswersParamsSchema =
  regenerateAnswersParamsSchema.extend({
    params: z.object({
      agentKey: z.string().min(1, { message: 'Agent key is required' }),
      conversationId: z
        .string()
        .regex(objectIdRegex, { message: 'Invalid message ID format' }),
      messageId: z
        .string()
        .regex(objectIdRegex, { message: 'Invalid message ID format' }),
    }),
    body: z.object({
      filters: z
        .object({
          apps: z.array(z.string()).optional(),
          kb: z.array(z.string()).optional(),
        })
        .optional(),
      modelKey: z
        .string()
        .min(1, { message: 'Model key is required' })
        .optional(),
      modelName: z
        .string()
        .min(1, { message: 'Model name is required' })
        .optional(),
      chatMode: z
        .string()
        .min(1, { message: 'Chat mode is required' })
        .optional(),
      modelFriendlyName: z
        .string()
        .min(1, { message: 'Model friendly name is required' })
        .optional(),
    }),
  });

export const updateFeedbackParamsSchema = regenerateAnswersParamsSchema;

/**
 * Schema for getting an enterprise search document by ID.
 */
export const enterpriseSearchGetSchema = z.object({
  params: z.object({
    conversationId: z.string().regex(objectIdRegex, {
      message: 'ID must be a valid MongoDB ObjectId',
    }),
  }),
});

/**
 * Schema for deleting an enterprise search document.
 * (Same as get schema for ID validation.)
 */
export const enterpriseSearchDeleteSchema = enterpriseSearchGetSchema;

/**
 * Schema for searching enterprise search documents.
 * Validates query parameters:
 * - query (required)
 * - page and limit are optional numbers (with defaults)
 * - sortBy and sortOrder are optional and must be one of the allowed values if provided.
 */
export const enterpriseSearchQuerySchema = z.object({
  query: z.object({
    query: z
      .string({ required_error: 'Search query is required' })
      .min(1, { message: 'Search query is required' }),
    page: z.preprocess((arg) => Number(arg), z.number().min(1).default(1)),
    limit: z.preprocess(
      (arg) => Number(arg),
      z.number().min(1).max(100).default(10),
    ),
    sortBy: z.enum(['createdAt', 'title']).optional(),
    sortOrder: z.enum(['asc', 'desc']).optional(),
  }),
});

export const enterpriseSearchSearchSchema = z.object({
  body: z.object({
    query: z.string().min(1, { message: 'Search query is required' }),
    filters: z
      .object({
        apps: z.array(z.string()).optional(),
        kb: z.array(z.string()).optional(),
      })
      .optional(),
    limit: z
      .preprocess((arg) => Number(arg), z.number().min(1).max(100).default(10))
      .optional(),
    modelKey: z
      .string()
      .min(1, { message: 'Model key is required' })
      .optional(),
    modelName: z
      .string()
      .min(1, { message: 'Model name is required' })
      .optional(),
    chatMode: z
      .string()
      .min(1, { message: 'Chat mode is required' })
      .optional(),
    modelFriendlyName: z
      .string()
      .min(1, { message: 'Model friendly name is required' })
      .optional(),
  }),
});

export const enterpriseSearchSearchHistorySchema = z.object({
  params: z.object({
    limit: z
      .preprocess((arg) => Number(arg), z.number().min(1).max(100).default(10))
      .optional(),
    page: z
      .preprocess((arg) => Number(arg), z.number().min(1).default(1))
      .optional(),
  }),
});

export const searchIdParamsSchema = z.object({
  params: z.object({
    searchId: z
      .string()
      .regex(objectIdRegex, { message: 'Invalid search ID format' }),
  }),
});

export const searchShareParamsSchema = searchIdParamsSchema.extend({
  body: z.object({
    userIds: z.array(z.string().regex(objectIdRegex)).min(1, {
      message: 'At least one user ID is required',
    }),
    accessLevel: z.enum(['read', 'write']).optional(),
  }),
});

/**
 * =========================
 * Agent Routes - Validation
 * =========================
 */

// params: { agentKey }
export const agentKeyParamsOnlySchema = z.object({
  params: z.object({
    agentKey: z.string().min(1, { message: 'Agent key is required' }),
  }),
});

// params: { agentKey, conversationId }
export const agentConversationParamsSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1, { message: 'Agent key is required' }),
    conversationId: z
      .string()
      .regex(objectIdRegex, { message: 'Invalid conversation ID format' }),
  }),
});

// body used for agent chat stream
const agentChatBodyBaseSchema = z.object({
  query: z.string().min(1, { message: 'Query is required' }),
  previousConversations: z.array(z.record(z.any())).optional(),
  recordIds: z.array(z.string().regex(objectIdRegex)).optional(),
  filters: z
    .object({
      apps: z.array(z.string()).optional(),
      kb: z.array(z.string()).optional(),
    })
    .optional(),
  tools: z.array(z.any()).optional(),
  chatMode: z.string().min(1).optional(),
  modelKey: z.string().min(1).optional(),
  modelName: z.string().min(1).optional(),
  modelFriendlyName: z.string().min(1).optional(),
  timezone: z.string().optional(),
  currentTime: z.string().optional(),
});

// POST /:agentKey/conversations/stream
export const agentStreamSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1, { message: 'Agent key is required' }),
  }),
  body: agentChatBodyBaseSchema,
});

// POST /:agentKey/conversations/:conversationId/messages/stream
export const agentAddMessageSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1, { message: 'Agent key is required' }),
    conversationId: z
      .string()
      .regex(objectIdRegex, { message: 'Invalid conversation ID format' }),
  }),
  body: agentChatBodyBaseSchema,
});

// Create/Update Agent payloads
const modelEntrySchema = z.object({
  modelKey: z.string().min(1, { message: 'modelKey is required' }),
  modelName: z.string().min(1, { message: 'modelName is required' }),
  provider: z.string().optional(),
  isReasoning: z.boolean().optional(),
});

const toolItemSchema = z.object({
  name: z.string().min(1),
  fullName: z.string().min(1),
  description: z.string().optional(),
});

const toolsetItemSchema = z.object({
  id: z.string().uuid().optional(),
  name: z.string().min(1),
  displayName: z.string().min(1),
  type: z.string().min(1),
  tools: z.array(toolItemSchema).default([]),
  instanceId: z.string().uuid().optional(),
});

const knowledgeItemSchema = z.object({
  id: z.string().optional(),
  connectorId: z.string(),
  filters: z.record(z.any()).optional(),
});

export const createAgentSchema = z.object({
  body: z.object({
    name: z.string().min(1).max(200),
    description: z.string().optional(),
    systemPrompt: z.string().optional(),
    startMessage: z.string().optional(),
    instructions: z.string().optional(),
    models: z.array(modelEntrySchema).min(1, { message: 'At least one model is required' }),
    tags: z.array(z.string()).optional(),
    toolsets: z.array(toolsetItemSchema).optional(),
    knowledge: z.array(knowledgeItemSchema).optional(),
    shareWithOrg: z.boolean().optional(),
  }),
});

export const updateAgentSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1, { message: 'Agent key is required' }),
  }),
  body: z.object({
    name: z.string().min(1).max(200).optional(),
    description: z.string().optional(),
    systemPrompt: z.string().optional(),
    startMessage: z.string().optional(),
    instructions: z.string().optional(),
    models: z.array(modelEntrySchema).min(1).optional(),
    tags: z.array(z.string()).optional(),
    toolsets: z.array(toolsetItemSchema).optional(),
    knowledge: z.array(knowledgeItemSchema).optional(),
    shareWithOrg: z.boolean().optional(),
  }),
});

/**
 * =========================
 * Response Schemas (Zod)
 * =========================
 */

const metaSchema = z.object({
  requestId: z.any().optional(),
  timestamp: z.string(),
  duration: z.number().or(z.string()),
});

const paginationSchema = z.object({
  page: z.number(),
  limit: z.number(),
  totalCount: z.number(),
  totalPages: z.number(),
  hasNext: z.coerce.boolean(),
  hasPrev: z.coerce.boolean(),
});

// ----- Strongly-typed entities (derived from Mongoose schemas) -----
export const messageCitationSchemaZ = z.object({
  citationId: z.any().optional(),
  relevanceScore: z.number().min(0).max(1).optional(),
  excerpt: z.string().optional(),
  context: z.string().optional(),
});

export const followUpQuestionSchemaZ = z.object({
  question: z.string(),
  confidence: z.enum(['Low', 'Medium', 'High']),
  reasoning: z.string().optional(),
});

export const feedbackEntrySchemaZ = z.object({
  isHelpful: z.boolean().optional(),
  ratings: z
    .object({
      accuracy: z.number().min(1).max(5).optional(),
      relevance: z.number().min(1).max(5).optional(),
      completeness: z.number().min(1).max(5).optional(),
      clarity: z.number().min(1).max(5).optional(),
    })
    .optional(),
  categories: z.array(z.string()).optional(),
  comments: z
    .object({
      positive: z.string().optional(),
      negative: z.string().optional(),
      suggestions: z.string().optional(),
    })
    .optional(),
  citationFeedback: z
    .array(
      z.object({
        citationId: z.any().optional(),
        isRelevant: z.boolean().optional(),
        relevanceScore: z.number().min(1).max(5).optional(),
        comment: z.string().optional(),
      }),
    )
    .optional(),
  followUpQuestionsHelpful: z.boolean().optional(),
  unusedFollowUpQuestions: z.array(z.string()).optional(),
  source: z.enum(['user', 'system', 'admin', 'auto']).optional(),
  feedbackProvider: z.any().optional(),
  timestamp: z.number().optional(),
  revisions: z
    .array(
      z.object({
        updatedFields: z.array(z.string()).optional(),
        previousValues: z.record(z.any()).optional(),
        updatedBy: z.any().optional(),
        updatedAt: z.number().optional(),
      }),
    )
    .optional(),
  metrics: z
    .object({
      timeToFeedback: z.number().optional(),
      userInteractionTime: z.number().optional(),
      feedbackSessionId: z.string().optional(),
      userAgent: z.string().optional(),
      platform: z.string().optional(),
    })
    .optional(),
});

export const referenceDataItemSchemaZ = z.object({
  name: z.string().optional(),
  id: z.string().optional(),
  type: z.string().optional(),
  key: z.string().optional(),
  accountId: z.string().optional(),
});

export const aiModelInfoSchemaZ = z.object({
  modelKey: z.string().nullable().optional(),
  modelName: z.string().nullable().optional(),
  modelProvider: z.string().nullable().optional(),
  chatMode: z.string().nullable().optional(),
  modelFriendlyName: z.string().nullable().optional(),
});

export const messageSchemaZ = z.object({
  _id: z.any().optional(),
  messageType: z.enum(['user_query', 'bot_response', 'error', 'feedback', 'system']),
  content: z.string(),
  contentFormat: z.enum(['MARKDOWN', 'JSON', 'HTML']).optional(),
  citations: z.array(messageCitationSchemaZ).optional(),
  confidence: z.enum(['Low', 'Medium', 'High']).nullable().optional(),
  followUpQuestions: z.array(followUpQuestionSchemaZ).optional(),
  feedback: z.array(feedbackEntrySchemaZ).optional(),
  metadata: z
    .object({
      processingTimeMs: z.number().optional(),
      modelVersion: z.string().optional(),
      aiTransactionId: z.string().optional(),
    })
    .optional(),
  modelInfo: aiModelInfoSchemaZ.optional(),
  referenceData: z.array(referenceDataItemSchemaZ).optional(),
  createdAt: z.any().optional(),
  updatedAt: z.any().optional(),
});

export const conversationSchemaZ = z.object({
  _id: z.any().optional(),
  userId: z.any(),
  orgId: z.any(),
  title: z.string().nullable().optional(),
  initiator: z.any(),
  messages: z.array(messageSchemaZ),
  isShared: z.boolean().optional(),
  shareLink: z.string().nullable().optional(),
  sharedWith: z
    .array(
      z.object({
        userId: z.any(),
        accessLevel: z.enum(['read', 'write']),
      }),
    )
    .optional(),
  isDeleted: z.boolean().optional(),
  deletedBy: z.any().nullable().optional(),
  isArchived: z.boolean().optional(),
  archivedBy: z.any().nullable().optional(),
  lastActivityAt: z.number().optional(),
  status: z.enum(['None', 'Inprogress', 'Complete', 'Failed']).nullable().optional(),
  failReason: z.string().nullable().optional(),
  modelInfo: aiModelInfoSchemaZ.optional(),
  conversationErrors: z
    .array(
      z.object({
        message: z.string(),
        errorType: z.string().optional(),
        timestamp: z.any().optional(),
        messageId: z.any().optional(),
        stack: z.string().optional(),
        metadata: z.record(z.any()).optional(),
      }),
    )
    .optional(),
  metadata: z.record(z.any()).optional(),
  createdAt: z.any().optional(),
  updatedAt: z.any().optional(),
});

// Lightweight list item for regular conversations (messages excluded in list views)
export const conversationListItemSchemaZ = conversationSchemaZ
  .extend({
    messages: z.array(messageSchemaZ).optional(),
  })
  .passthrough();

export const agentConversationSchemaZ = conversationSchemaZ
  .extend({
    agentKey: z.string(),
    conversationSource: z.enum(['agent_chat']).optional(),
  })
  .passthrough();

// Lightweight list item (no messages payload included in list views)
export const agentConversationListItemSchemaZ = agentConversationSchemaZ.extend({
  messages: z.any().optional(),
}).passthrough();
// Agent DTOs (align with Python AgentOut)
export const agentToolSchemaZ = z.object({
  name: z.string(),
  fullName: z.string(),
  description: z.string().nullable().optional(),
});

export const agentToolsetSchemaZ = z.object({
  name: z.string(),
  displayName: z.string(),
  type: z.string(),
  tools: z.array(agentToolSchemaZ).default([]),
  instanceId: z.string().nullable().optional(),
});

export const agentKnowledgeSchemaZ = z.object({
  connectorId: z.string(),
  filters: z.record(z.any()).nullable().optional(),
});

export const agentModelOutSchemaZ = z.object({
  modelKey: z.string(),
  modelName: z.string(),
  provider: z.string().nullable().optional(),
  isReasoning: z.boolean().nullable().optional(),
  isMultimodal: z.boolean().nullable().optional(),
  isDefault: z.boolean().nullable().optional(),
  modelFriendlyName: z.string().nullable().optional(),
});

export const agentOutSchemaZ = z.object({
  _key: z.string().nullable().optional(),
  name: z.string(),
  description: z.string().nullable().optional(),
  startMessage: z.string().nullable().optional(),
  systemPrompt: z.string().nullable().optional(),
  instructions: z.string().nullable().optional(),
  models: z.union([z.array(agentModelOutSchemaZ), z.array(z.string())]),
  tags: z.array(z.string()).nullable().optional(),
  isActive: z.boolean().nullable().optional(),
  createdBy: z.string().nullable().optional(),
  updatedBy: z.string().nullable().optional(),
  createdAtTimestamp: z.number().nullable().optional(),
  updatedAtTimestamp: z.number().nullable().optional(),
  isDeleted: z.boolean().nullable().optional(),
  toolsets: z.array(agentToolsetSchemaZ).nullable().optional(),
  knowledge: z.array(agentKnowledgeSchemaZ).nullable().optional(),
  shareWithOrg: z.boolean().nullable().optional(),
});

// ----- Response schemas using strong entities -----
export const conversationsListResponseSchema = z.object({
  conversations: z.array(conversationListItemSchemaZ),
  sharedWithMeConversations: z.array(conversationListItemSchemaZ),
  pagination: paginationSchema,
  filters: z.any(),
  meta: metaSchema,
});

export const conversationByIdResponseSchema = z.object({
  conversation: conversationSchemaZ,
  filters: z.any(),
  meta: z
    .object({
      requestId: z.any().optional(),
      timestamp: z.string(),
      duration: z.number().or(z.string()),
      conversationId: z.any(),
      messageCount: z.number(),
    })
    .required(),
});

export const deleteConversationResponseSchema = z.object({
  id: z.any(),
  status: z.literal('deleted'),
  deletedAt: z.any(),
  deletedBy: z.any(),
  citationsDeleted: z.number(),
  meta: metaSchema,
});

export const archiveConversationResponseSchema = z.object({
  id: z.any(),
  status: z.enum(['archived', 'unarchived']),
  archivedBy: z.any().optional(),
  archivedAt: z.any().optional(),
  unarchivedBy: z.any().optional(),
  unarchivedAt: z.any().optional(),
  meta: metaSchema,
});

export const listArchivesResponseSchema = z.object({
  conversations: z.array(conversationSchemaZ),
  pagination: paginationSchema,
  filters: z.any(),
  summary: z
    .object({
      totalArchived: z.number(),
      oldestArchive: z.any().optional(),
      newestArchive: z.any().optional(),
    })
    .optional(),
  meta: metaSchema,
});

export const enterpriseSearchResponseSchema = z.object({
  searchId: z.any().optional(),
  searchResponse: z.any().optional(),
});

export const simpleMessageResponseSchema = z.object({
  message: z.string(),
});

// Agent responses (proxied from Python)
export const agentDetailResponseSchema = z.object({
  status: z.string(),
  message: z.string(),
  agent: agentOutSchemaZ,
});

export const agentListResponseSchema = z.object({
  success: z.boolean(),
  agents: z.array(agentOutSchemaZ),
  pagination: z.object({
    currentPage: z.number(),
    limit: z.number(),
    totalItems: z.number(),
    totalPages: z.number(),
    hasNext: z.boolean(),
    hasPrev: z.boolean(),
  }),
});

// Agent conversation responses use the richer agentConversationSchemaZ
export const agentConversationsListResponseSchema = z.object({
  conversations: z.array(agentConversationListItemSchemaZ),
  sharedWithMeConversations: z.array(agentConversationListItemSchemaZ),
  pagination: paginationSchema,
  filters: z.any(),
  meta: metaSchema,
});

export const agentConversationByIdResponseSchema = z.object({
  conversation: agentConversationSchemaZ,
  filters: z.any(),
  meta: z
    .object({
      requestId: z.any().optional(),
      timestamp: z.string(),
      duration: z.number().or(z.string()),
      conversationId: z.any(),
      messageCount: z.number(),
    })
    .required(),
});

export const agentCreateResponseSchema = z.object({
  status: z.string(),
  message: z.string(),
  agent: agentOutSchemaZ,
  warnings: z.array(z.record(z.any())).nullable().optional(),
});

// Update/Delete agent responses
export const agentUpdateResponseSchema = z.object({
  status: z.string(),
  message: z.string(),
});

export const agentDeleteResponseSchema = z.object({
  status: z.string(),
  message: z.string(),
  deleted: z.object({
    agents: z.number().optional(),
    toolsets: z.number().optional(),
    tools: z.number().optional(),
    knowledge: z.number().optional(),
    edges: z.number().optional(),
  }),
});

export const updateTitleResponseSchema = z.object({
  conversation: conversationSchemaZ,
  meta: metaSchema,
});

export const updateFeedbackResponseSchema = z.object({
  conversationId: z.any(),
  messageId: z.any(),
  feedback: z.any(),
  meta: metaSchema,
});

export const shareConversationResponseSchema = z.object({
  id: z.any(),
  isShared: z.boolean(),
  shareLink: z.any().optional(),
  sharedWith: z.any(),
  meta: metaSchema,
});

export const unshareConversationResponseSchema = z.object({
  id: z.any(),
  isShared: z.boolean(),
  shareLink: z.any().optional(),
  sharedWith: z.any(),
  unsharedUsers: z.array(z.string()),
  meta: metaSchema,
});

export const searchHistoryResponseSchema = z.object({
  searchHistory: z.array(z.any()),
  pagination: paginationSchema,
  filters: z.any(),
  meta: metaSchema,
});

export const searchByIdResponseSchema = z.any();

export const deleteAgentConversationResponseSchema = z.object({
  message: z.string(),
  conversation: z.any(),
});