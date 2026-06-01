import { z } from 'zod';
import {
  validateNoFormatSpecifiers,
  validateNoXSS,
} from '../../../utils/xss-sanitization';
import { PIPESHUB_CHAT_MODE } from '../constants/constants';

// ---------------------------------------------------------------------------
// Primitive validators
// ---------------------------------------------------------------------------

/** Regular expression for MongoDB ObjectId validation. */
const OBJECT_ID_REGEX = /^[0-9a-fA-F]{24}$/;

/** Reusable MongoDB ObjectId string validator with a configurable error label. */
const objectId = (label: string) =>
  z.string().regex(OBJECT_ID_REGEX, { message: `Invalid ${label} format` });

/** Allow UUID or Collection app ID: knowledgeBase_<orgId> */
const appOrKbIdSchema = z.string().refine(
  (val) =>
    z.string().uuid().safeParse(val).success ||
    /^knowledgeBase_[a-zA-Z0-9_-]+$/.test(val),
  { message: 'Must be a valid UUID or knowledgeBase_<orgId> format' },
);

/** Common pagination preprocessor: coerce string -> number with bounds. */
const pageSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).default(1),
);

const limitSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).max(100).default(10),
);

/** Limit preprocessor for conversation list endpoints (default 20). */
const conversationListLimitSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).max(100).default(20),
);

/** Page preprocessor for archived endpoints (max 1000, default 1). */
const archivePageSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).max(1000).default(1),
);

/** Limit preprocessor for archived endpoints (max 100, default 20). */
const archiveLimitSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).max(100).default(20),
);

/** Limit preprocessor for agent list endpoint (Python backend allows up to 200). */
const agentListLimitSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).max(200).default(20),
);

// ---------------------------------------------------------------------------
// Reusable sub-schemas
// ---------------------------------------------------------------------------

/** Rich filter node (appliedFilters) — used for display/persistence only. */
const appliedFilterNodeSchema = z.object({
  id: z.string(),
  name: z.string(),
  nodeType: z.string(),
  connector: z.string(),
});

const appliedFiltersSchema = z
  .object({
    apps: z.array(appliedFilterNodeSchema).optional(),
    kb: z.array(appliedFilterNodeSchema).optional(),
  })
  .optional();

/** `{ apps, kb }` filter object reused across search/message/regenerate flows. */
const filtersSchema = z
  .object({
    apps: z.array(appOrKbIdSchema).optional(),
    kb: z.array(appOrKbIdSchema).optional(),
  })
  .optional();

/** Model selection fields shared across search, message, regenerate, etc. */
const modelFieldsSchema = {
  modelKey: z
    .string()
    .min(1, { message: 'Model key is required' })
    .optional(),
  modelName: z
    .string()
    .min(1, { message: 'Model name is required' })
    .optional(),
  modelFriendlyName: z
    .string()
    .min(1, { message: 'Model friendly name is required' })
    .optional(),
};

/** Execution-context fields: timezone, currentTime, tools. */
const contextFieldsSchema = {
  timezone: z
    .string()
    .min(1, { message: 'Timezone must be a non-empty string' })
    .optional(),
  currentTime: z
    .string()
    .datetime({
      offset: true,
      message: 'currentTime must be an ISO 8601 datetime string',
    })
    .optional(),
  tools: z.array(z.string().min(1)).optional(),
};

/** Title body shared by conversation/agent rename endpoints. */
const titleBodySchema = z.object({
  title: z
    .string()
    .min(1, { message: 'Title is required' })
    .max(200, { message: 'Title must be less than 200 characters' }),
});

/** UserIds array used by share endpoints. */
const userIdsSchema = z
  .array(objectId('user ID'))
  .min(1, { message: 'At least one user ID is required' });

// ---------------------------------------------------------------------------
// Reusable param shapes
// ---------------------------------------------------------------------------

const conversationIdParam = { conversationId: objectId('conversation ID') };
const messageIdParam = { messageId: objectId('message ID') };
const agentKeyParam = {
  agentKey: z.string().min(1, { message: 'Agent key is required' }),
};

// ---------------------------------------------------------------------------
// Enterprise search: create
// ---------------------------------------------------------------------------
const attachmentRefSchema = z.object({
  recordId: z.string().min(1, { message: 'Attachment recordId is required' }),
  recordName: z.string().min(1).optional(),
  mimeType: z.string().min(1).optional(),
  extension: z.string().min(1).optional(),
  virtualRecordId: z.string().min(1).optional(),
});

const enterpriseSearchCreateBodySchema = z.object({
    query: z
      .string({ required_error: 'Query is required' })
      .min(1, { message: 'Query is required' })
      .max(100000, {
        message: 'Query exceeds maximum length of 100000 characters',
      }),
    recordIds: z.array(objectId('record ID')).optional(),
    filters: filtersSchema,
    appliedFilters: appliedFiltersSchema,
    attachments: z.array(attachmentRefSchema).optional(),
    chatMode: z.nativeEnum(PIPESHUB_CHAT_MODE).optional(),
    ...modelFieldsSchema,
    ...contextFieldsSchema,
});

export const enterpriseSearchCreateSchema = z.object({
  body: enterpriseSearchCreateBodySchema,
});

// ---------------------------------------------------------------------------
// Conversation params / title / share
// ---------------------------------------------------------------------------

export const conversationIdParamsSchema = z.object({
  params: z.object(conversationIdParam),
});

export const conversationTitleParamsSchema = conversationIdParamsSchema.extend({
  body: titleBodySchema,
});

export const conversationShareParamsSchema = conversationIdParamsSchema.extend({
  body: z.object({ userIds: userIdsSchema }),
});

// ---------------------------------------------------------------------------
// Agent conversation params / title
// ---------------------------------------------------------------------------

export const agentConversationParamsSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
  }),
});

/** Schema for DELETE /:agentKey/conversations/:conversationId — delete one agent conversation. */
export const deleteAgentConversationParamsSchema = agentConversationParamsSchema;

export const agentConversationTitleParamsSchema =
  agentConversationParamsSchema.extend({
    body: titleBodySchema,
  });

/** Schema for GET /:agentKey/conversations/:conversationId — fetch one agent conversation with message pagination/filtering. */
export const getAgentConversationByIdSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
  }),
  query: z.object({
    page: pageSchema.optional().default(1),
    limit: conversationListLimitSchema.optional().default(20),
    sortBy: z
      .enum(['createdAt', 'messageType', 'content'])
      .optional()
      .default('createdAt'),
    sortOrder: z
      .string()
      .optional()
      .transform((value) => (value === 'asc' ? 'asc' : 'desc')),
    startDate: z
      .preprocess((value) => (value === '' ? undefined : value), z.string().optional())
      .refine(
        (value) => !value || !isNaN(new Date(value).getTime()),
        'Invalid start date format',
      ),
    endDate: z
      .preprocess((value) => (value === '' ? undefined : value), z.string().optional())
      .refine(
        (value) => !value || !isNaN(new Date(value).getTime()),
        'Invalid end date format',
      ),
    messageType: z
      .preprocess((value) => (value === '' ? undefined : value), z.string().optional())
      .refine(
        (value) =>
          !value ||
          ['user_query', 'bot_response', 'error', 'feedback', 'system'].includes(
            value,
          ),
        'Invalid message type. Must be one of: user_query, bot_response, error, feedback, system',
      ),
  }),
});

// ---------------------------------------------------------------------------
// Add message
// ---------------------------------------------------------------------------

const addMessageBodySchema = z.object({
    query: z.string().min(1, { message: 'Query is required' }),
    filters: filtersSchema,
    appliedFilters: appliedFiltersSchema,
    attachments: z.array(attachmentRefSchema).optional(),
    chatMode: z.nativeEnum(PIPESHUB_CHAT_MODE).optional(),
    ...modelFieldsSchema,
    ...contextFieldsSchema,
});

export const addMessageParamsSchema = z.object({
  params: z.object(conversationIdParam),
  body: addMessageBodySchema,
});

/** Agent follow-up stream chat modes (matches OpenAPI AgentAddMessageStreamRequest). */
export const AGENT_CHAT_MODES = ['auto', 'quick', 'verification', 'deep'] as const;

const agentChatModeSchema = z
  .enum(AGENT_CHAT_MODES, {
    errorMap: () => ({ message: 'Invalid chat mode' }),
  })
  .optional();

const agentAddMessageBodySchema = addMessageBodySchema.extend({
  chatMode: agentChatModeSchema 
});

const agentStreamCreateBodySchema = enterpriseSearchCreateBodySchema.extend({
  chatMode: agentChatModeSchema, // TODO: remove this
});

// ---------------------------------------------------------------------------
// Agent stream: create + add message
// ---------------------------------------------------------------------------

export const agentStreamCreateSchema = z.object({
  params: z.object(agentKeyParam),
  body: agentStreamCreateBodySchema,
});

export const agentAddMessageParamsSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
  }),
  body: agentAddMessageBodySchema,
});

// ---------------------------------------------------------------------------
// Agent create (POST /agents/create) — gateway guardrails; Python enforces semantics
// ---------------------------------------------------------------------------

/** Defensive max length for long agent text fields (not enforced by Python). */
const agentLongTextSchema = z.string().max(100_000);

const agentToolRefSchema = z
  .object({
    name: z.string().trim().min(1),
    fullName: z.string().trim().min(1).optional(),
    description: z.string().max(10_000).optional(),
  });

/**
 * Toolset names exposed by the Python registry (non-internal) and used by agent builder payloads.
 * Keep this list aligned with OpenAPI `AgentCreateToolsetName`.
 */
export const AGENT_CREATE_TOOLSET_NAMES = [
  'calendar',
  'clickup',
  'confluence',
  'drive',
  'github',
  'gmail',
  'jira',
  'lumos',
  'mariadb',
  'onedrive',
  'outlook',
  'redshift',
  'salesforce',
  'sharepoint',
  'slack',
  'teams',
  'zoom',
] as const;

const agentToolsetNameSchema = z.enum(AGENT_CREATE_TOOLSET_NAMES, {
  errorMap: () => ({ message: 'Invalid toolset name' }),
});

const agentToolsetSchema = z
  .object({
    name: z.string().trim().pipe(agentToolsetNameSchema),
    displayName: z.string().max(200).optional(),
    type: z.string().max(100).optional(),
    instanceId: z.string().max(256).optional(),
    instanceName: z.string().max(200).optional(),
    tools: z.array(agentToolRefSchema).optional(),
  });

const agentKnowledgeSchema = z
  .object({
    connectorId: z.string().trim().min(1),
    filters: z.union([z.record(z.unknown()), z.string(), z.array(z.unknown())]).optional(),
  });

const agentModelEntrySchema = z.union([
  z.string().trim().min(1),
  z
    .object({
      modelKey: z.string().trim().min(1),
      modelName: z.string().optional(),
      isReasoning: z.boolean().optional(),
      provider: z.string().optional(),
    }),
]);

const agentWebSearchSchema = z.union([
  z.string().trim().min(1),
  z
    .object({
      provider: z.string().trim().min(1),
      providerKey: z.string().max(256).optional(),
      providerLabel: z.string().max(200).optional(),
      iconPath: z.string().max(500).optional(),
    }),
]);

const createAgentBodySchema = z
  .object({
    name: z
      .string({ required_error: 'Name is required' })
      .trim()
      .min(1, { message: 'Name is required' })
      .max(200, { message: 'Name must be less than 200 characters' }),
    models: z
      .array(agentModelEntrySchema)
      .min(1, {
        message:
          'At least one AI model is required. Please add a model to your configuration.',
      }),
    description: agentLongTextSchema.optional(),
    startMessage: agentLongTextSchema.optional(),
    systemPrompt: agentLongTextSchema.optional(),
    instructions: agentLongTextSchema.optional(),
    tags: z.array(z.string().max(100)).max(50).optional(),
    shareWithOrg: z.boolean().optional(),
    isServiceAccount: z.boolean().optional(),
    toolsets: z.array(agentToolsetSchema).max(100).optional(),
    knowledge: z.array(agentKnowledgeSchema).max(100).optional(),
    webSearch: z.union([z.null(), agentWebSearchSchema]).optional(),
  });

export const createAgentSchema = z.object({
  body: createAgentBodySchema,
});

// ---------------------------------------------------------------------------
// Agent update schema (PUT /:agentKey)
// ---------------------------------------------------------------------------

const updateAgentBodySchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, { message: 'Name is required' })
      .max(200, { message: 'Name must be less than 200 characters' })
      .optional(),
    models: z.array(agentModelEntrySchema).min(1).optional(),
    description: agentLongTextSchema.optional(),
    startMessage: agentLongTextSchema.optional(),
    systemPrompt: agentLongTextSchema.optional(),
    instructions: agentLongTextSchema.optional(),
    tags: z.array(z.string().max(100)).max(50).optional(),
    shareWithOrg: z.boolean().optional(),
    isServiceAccount: z.boolean().optional(),
    toolsets: z.array(agentToolsetSchema).max(100).optional(),
    knowledge: z.array(agentKnowledgeSchema).max(100).optional(),
    webSearch: z.union([z.null(), agentWebSearchSchema]).optional(),
  });

export const updateAgentSchema = z.object({
  params: z.object(agentKeyParam),
  body: updateAgentBodySchema,
});

// ---------------------------------------------------------------------------
// Agent get / list query schemas
// ---------------------------------------------------------------------------

export const getAgentParamsSchema = z.object({
  params: z.object(agentKeyParam),
});

// ---------------------------------------------------------------------------
// Agent delete schema (DELETE /:agentKey) — path param only; no body/query
// ---------------------------------------------------------------------------

export const deleteAgentSchema = z.object({
  params: z.object(agentKeyParam),
});

// ---------------------------------------------------------------------------
// Agent list query schema
// ---------------------------------------------------------------------------

export const listAgentsQuerySchema = z.object({
  query: z.object({
    page: pageSchema,
    limit: agentListLimitSchema,
    search: z
      .string()
      .trim()
      .min(1)
      .max(1000, { message: 'Search parameter too long (max 1000 characters)' })
      .optional()
      .superRefine((value, ctx) => {
        if (!value) {
          return;
        }

        try {
          validateNoXSS(value, 'search parameter');
          validateNoFormatSpecifiers(value, 'search parameter');
        } catch (error: any) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: error.message,
          });
        }
      }),
    sort_by: z.string().trim().min(1).max(100).optional().default('updatedAtTimestamp'),
    sort_order: z.enum(['asc', 'desc']).optional().default('desc'),
  }),
});

// ---------------------------------------------------------------------------
// Message params
// ---------------------------------------------------------------------------

export const messageIdParamsSchema = z.object({
  params: z.object(messageIdParam),
});

// ---------------------------------------------------------------------------
// Regenerate answers (conversation + agent)
// ---------------------------------------------------------------------------

const regenerateBodySchema = z.object({
  filters: filtersSchema,
  chatMode: z.string().min(1, { message: 'Chat mode is required' }).optional(),
  ...modelFieldsSchema,
  ...contextFieldsSchema,
});

export const regenerateAnswersParamsSchema = z.object({
  params: z.object({
    ...conversationIdParam,
    ...messageIdParam,
  }),
  body: regenerateBodySchema,
});

export const regenerateAgentAnswersParamsSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
    ...messageIdParam,
  }),
  body: regenerateBodySchema,
});

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

export const FEEDBACK_CATEGORIES = [
  'incorrect_information',
  'missing_information',
  'irrelevant_information',
  'unclear_explanation',
  'poor_citations',
  'excellent_answer',
  'helpful_citations',
  'well_explained',
  'other',
] as const;

const feedbackBodySchema = z.object({
  isHelpful: z.boolean().optional(),
  categories: z.array(z.enum(FEEDBACK_CATEGORIES)).optional(),
  comments: z
    .object({
      positive: z.string().optional(),
      negative: z.string().optional(),
    })
    .optional(),
});

export const updateFeedbackParamsSchema = z.object({
  params: z.object({
    ...conversationIdParam,
    ...messageIdParam,
  }),
  body: feedbackBodySchema,
});

export const updateAgentFeedbackParamsSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
    ...messageIdParam,
  }),
  body: feedbackBodySchema,
});

// ---------------------------------------------------------------------------
// Enterprise search: get / delete / search / history
// ---------------------------------------------------------------------------

/** Schema for getting an enterprise search document by ID. */
export const enterpriseSearchGetSchema = z.object({
  params: z.object(conversationIdParam),
});

/** Schema for deleting an enterprise search document (same shape as get). */
export const enterpriseSearchDeleteSchema = enterpriseSearchGetSchema;

/**
 * Schema for searching enterprise search documents via query string.
 * - query (required)
 * - page, limit are optional numbers (with defaults)
 * - sortBy, sortOrder are optional enums
 */
export const enterpriseSearchQuerySchema = z.object({
  query: z.object({
    query: z
      .string({ required_error: 'Search query is required' })
      .min(1, { message: 'Search query is required' }),
    page: pageSchema,
    limit: limitSchema,
    sortBy: z.enum(['createdAt', 'title']).optional(),
    sortOrder: z.enum(['asc', 'desc']).optional(),
  }),
});

export const enterpriseSearchSearchSchema = z.object({
  body: z.object({
    query: z.string().min(1, { message: 'Search query is required' }),
    filters: filtersSchema,
    limit: limitSchema.optional(),
  }),
});

export const enterpriseSearchSearchHistorySchema = z.object({
  params: z.object({
    limit: limitSchema.optional(),
    page: pageSchema.optional(),
  }),
});

// ---------------------------------------------------------------------------
// Search params / share
// ---------------------------------------------------------------------------

export const searchIdParamsSchema = z.object({
  params: z.object({ searchId: objectId('search ID') }),
});

export const searchShareParamsSchema = searchIdParamsSchema.extend({
  body: z.object({
    userIds: userIdsSchema,
    accessLevel: z.enum(['read', 'write']).optional(),
  }),
});

// ---------------------------------------------------------------------------
// Conversation list / archive query schemas
// ---------------------------------------------------------------------------

/** Schema for GET /conversations — list all conversations with filters. */
export const getAllConversationsQuerySchema = z.object({
  query: z.object({
    source: z.enum(['owned', 'shared']).optional().default('owned'),
    page: pageSchema,
    limit: limitSchema,
    sortBy: z.enum(['createdAt', 'lastActivityAt', 'title']).optional(),
    sortOrder: z.enum(['asc', 'desc']).optional(),
    conversationId: objectId('conversation ID').optional(),
    search: z.string().max(1000).optional(),
    startDate: z.string().datetime({ offset: true }).optional(),
    endDate: z.string().datetime({ offset: true }).optional(),
    shared: z.enum(['true', 'false', '1', '0']).optional(),
  }),
});

/** Schema for GET /:agentKey/conversations — list agent conversations with filters. */
export const getAllAgentConversationsQuerySchema = z.object({
  params: z.object({
    ...agentKeyParam,
  }),
  query: z.object({
    page: pageSchema.optional().default(1),
    limit: conversationListLimitSchema.optional().default(20),
    sortBy: z
      .string()
      .optional()
      .transform((value) =>
        value === 'createdAt' || value === 'lastActivityAt' || value === 'title'
          ? value
          : undefined,
      ),
    sortOrder: z
      .string()
      .optional()
      .transform((value) =>
        value === 'asc' || value === 'desc' ? value : undefined,
      ),
    search: z
      .string()
      .max(1000, { message: 'Search parameter too long (max 1000 characters)' })
      .optional()
      .superRefine((value, ctx) => {
        if (!value) {
          return;
        }

        try {
          validateNoXSS(value, 'search parameter');
          validateNoFormatSpecifiers(value, 'search parameter');
        } catch (error: any) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: error.message,
          });
        }
      }),
    startDate: z
      .string()
      .optional()
      .refine(
        (value) => !value || !isNaN(new Date(value).getTime()),
        'Invalid start date format',
      ),
    endDate: z
      .string()
      .optional()
      .refine(
        (value) => !value || !isNaN(new Date(value).getTime()),
        'Invalid end date format',
      ),
    status: z.preprocess(
      (value) => (value === '' ? undefined : value),
      z.string().optional(),
    ),
    isArchived: z.enum(['true', 'false']).optional(),
  }),
});

/** Schema for GET /agents/:agentKey/conversations/show/archives — list archived conversations for one agent. */
export const listAllArchivesAgentConversationQuerySchema = z.object({
  params: z.object({
    ...agentKeyParam,
  }),
  query: z.object({
    page: z
      .preprocess((arg) => {
        if (arg === undefined || arg === '') {
          return undefined;
        }
        const parsed = parseInt(String(arg), 10);
        return Number.isNaN(parsed) ? undefined : parsed;
      }, z.number().optional())
      .transform((value) => {
        if (value === undefined || value < 1) {
          return 1;
        }
        return value;
      }),
    limit: z
      .preprocess((arg) => {
        if (arg === undefined || arg === '') {
          return undefined;
        }
        const parsed = parseInt(String(arg), 10);
        return Number.isNaN(parsed) ? undefined : parsed;
      }, z.number().optional())
      .transform((value) => {
        if (value === undefined || value < 1 || value > 100) {
          return 20;
        }
        return value;
      }),
    sortBy: z
      .string()
      .optional()
      .transform((value) =>
        value === 'createdAt' || value === 'lastActivityAt' || value === 'title'
          ? value
          : undefined,
      ),
    sortOrder: z
      .string()
      .optional()
      .transform((value) => (value === 'asc' || value === 'desc' ? value : undefined)),
    search: z
      .string()
      .max(1000, { message: 'Search parameter too long (max 1000 characters)' })
      .optional()
      .superRefine((value, ctx) => {
        if (!value) {
          return;
        }

        try {
          validateNoXSS(value, 'search parameter');
          validateNoFormatSpecifiers(value, 'search parameter');
        } catch (error: any) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: error.message,
          });
        }
      }),
    startDate: z
      .string()
      .optional()
      .refine(
        (value) => !value || !isNaN(new Date(value).getTime()),
        'Invalid start date format',
      ),
    endDate: z
      .string()
      .optional()
      .refine(
        (value) => !value || !isNaN(new Date(value).getTime()),
        'Invalid end date format',
      ),
  }),
});

/** Schema for GET /conversations/show/archives — list archived conversations. */
export const listAllArchivesConversationQuerySchema = z.object({
  query: z.object({
    page: archivePageSchema,
    limit: archiveLimitSchema,
    sortBy: z
      .enum(['createdAt', 'lastActivityAt', 'title'])
      .optional()
      .default('lastActivityAt'),
    sortOrder: z.enum(['asc', 'desc']).optional().default('desc'),
    search: z.string().max(1000).optional(),
    shared: z.enum(['true', 'false', '1', '0']).optional(),
    startDate: z.string().datetime({ offset: true }).optional(),
    endDate: z.string().datetime({ offset: true }).optional(),
    conversationId: objectId('conversation ID').optional(),
  }),
});

/** Schema for GET /agents/conversations/show/archives — grouped archived agent conversations. */
export const listAllAgentsArchivedConversationsGroupedQuerySchema = z.object({
  params: z.object({}),
  query: z.object({
    agentPage: z
      .preprocess((arg) => {
        if (arg === undefined || arg === '') {
          return undefined;
        }
        const parsed = parseInt(String(arg), 10);
        return Number.isNaN(parsed) ? undefined : parsed;
      }, z.number().optional())
      .transform((value) => Math.max(1, value ?? 1)),
    agentLimit: z
      .preprocess((arg) => {
        if (arg === undefined || arg === '') {
          return undefined;
        }
        const parsed = parseInt(String(arg), 10);
        return Number.isNaN(parsed) ? undefined : parsed;
      }, z.number().optional())
      .transform((value) => Math.min(100, Math.max(1, value ?? 5))),
  }),
});

/** Schema for GET /conversations/show/archives/search — full-text search archived. */
export const searchArchivedConversationsQuerySchema = z.object({
  query: z.object({
    search: z.string().trim().min(1).max(1000),
    page: archivePageSchema,
    limit: archiveLimitSchema,
  }),
});

// ---------------------------------------------------------------------------
// Chat attachment upload / delete
// ---------------------------------------------------------------------------

const recordIdParam = {
  recordId: z
    .string()
    .trim()
    .min(1, { message: 'recordId is required' })
    .max(256, { message: 'recordId is too long' }),
}

const attachmentUploadBodySchema = z.object({
  conversationId: z
    .preprocess(
      (v) => (typeof v === 'string' ? v.trim() : v),
      z.union([
        z.literal(''),
        z.string().regex(OBJECT_ID_REGEX, { message: 'Invalid conversation ID format' }),
      ]),
    )
    .nullable()
    .optional(),
});

export const attachmentUploadSchema = z.object({
  body: attachmentUploadBodySchema,
});

export const attachmentRecordIdParamsSchema = z.object({
  params: z.object(recordIdParam),
});

export const agentAttachmentUploadSchema = z.object({
  params: z.object(agentKeyParam),
  body: attachmentUploadBodySchema,
});

export const agentAttachmentRecordIdParamsSchema = z.object({
  params: z.object({ ...agentKeyParam, ...recordIdParam }),
});
