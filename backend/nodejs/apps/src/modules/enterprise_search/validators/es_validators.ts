// es_schema.ts
import { z } from 'zod';

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
  (arg) => Number(arg),
  z.number().min(1).default(1),
);

const limitSchema = z.preprocess(
  (arg) => Number(arg),
  z.number().min(1).max(100).default(10),
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
  chatMode: z
    .string()
    .min(1, { message: 'Chat mode is required' })
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

const enterpriseSearchCreateBodySchema = z.object({
    query: z
      .string({ required_error: 'Query is required' })
      .min(1, { message: 'Query is required' })
      .max(100000, {
        message: 'Query exceeds maximum length of 100000 characters',
      }),
    recordIds: z.array(objectId('record ID')).optional(),
    departments: z.array(objectId('department ID')).optional(),
    filters: filtersSchema,
    appliedFilters: appliedFiltersSchema,
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

export const agentConversationTitleParamsSchema =
  agentConversationParamsSchema.extend({
    body: titleBodySchema,
  });

// ---------------------------------------------------------------------------
// Add message
// ---------------------------------------------------------------------------

const addMessageBodySchema = z.object({
    query: z.string().min(1, { message: 'Query is required' }),
    filters: filtersSchema,
    appliedFilters: appliedFiltersSchema,
    ...modelFieldsSchema,
    ...contextFieldsSchema,
});

export const addMessageParamsSchema = z.object({
  params: z.object(conversationIdParam),
  body: addMessageBodySchema,
});

// ---------------------------------------------------------------------------
// Agent stream: create + add message
// ---------------------------------------------------------------------------

export const agentStreamCreateSchema = z.object({
  params: z.object(agentKeyParam),
  body: enterpriseSearchCreateBodySchema,
});

export const agentAddMessageParamsSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
  }),
  body: addMessageBodySchema,
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
  ratings: z.record(z.string(), z.number().min(1).max(5)).optional(),
  categories: z.array(z.enum(FEEDBACK_CATEGORIES)).optional(),
  comments: z
    .object({
      positive: z.string().optional(),
      negative: z.string().optional(),
      suggestions: z.string().optional(),
    })
    .optional(),
  metrics: z
    .object({
      userInteractionTime: z.number().optional(),
      feedbackSessionId: z.string().optional(),
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
    ...modelFieldsSchema,
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