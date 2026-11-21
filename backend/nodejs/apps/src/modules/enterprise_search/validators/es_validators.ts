import { z } from 'zod';
import { sessionRepositorySchema } from '../../agent-harness/domain/repository';
import { runGatesSchema } from '../../agent-harness/domain/run-gates';
import { contract } from '../../agent-harness/contracts/zod-contract';
import {
  NAMED_ENTITY_KINDS,
  REQUEST_FILTER_KEYS,
  requestFiltersSchema,
} from '../../../libs/validators/request-filters';
import { keywordSearchBudgetSchema } from '../../../libs/validators/keyword-search-budget';
import {
  validateNoFormatSpecifiers,
  validateNoXSS,
} from '../../../utils/xss-sanitization';
import {
  PIPESHUB_CHAT_MODE,
  REASONING_EFFORT_VALUES,
} from '../constants/constants';
import {
  messageForUnknownOption,
  messageWhenMissing,
} from '../../../libs/utils/validation.utils';
import {
  datetimeWithOffsetCheck,
  guidCheck,
  integerCheck,
} from '../../../libs/utils/zod-formats';
import {
  createExtensionRegistry,
  listedReleaseNames,
} from '../../control-plane';
import {
  MAX_ROLE_MODELS,
  ROLE_KEY,
} from '../../agent-harness/domain/agent-definition';

export { NAMED_ENTITY_KINDS, REASONING_EFFORT_VALUES, REQUEST_FILTER_KEYS };

// ---------------------------------------------------------------------------
// Primitive validators
// ---------------------------------------------------------------------------

/** Regular expression for MongoDB ObjectId validation. */
const OBJECT_ID_REGEX = /^[0-9a-fA-F]{24}$/;

/** Reusable MongoDB ObjectId string validator with a configurable error label. */
const objectId = (label: string) =>
  z.string().regex(OBJECT_ID_REGEX, { message: `Invalid ${label} format` });

const appOrKbIdSchema = z.string().check(guidCheck('Must be a valid UUID'));

/** Common pagination preprocessor: coerce string -> number with bounds. */
const pageSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).default(1),
);

const limitSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().check(integerCheck()).min(1).max(100).default(10),
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
  // For the chip's file-type icon.
  extension: z.string().optional(),
  mimeType: z.string().optional(),
  // Named in the question with @, so it is shown as a chip there.
  mentioned: z.boolean().optional(),
});

const appliedFiltersSchema = z
  .object({
    apps: z.array(appliedFilterNodeSchema).optional(),
    kb: z.array(appliedFilterNodeSchema).optional(),
    recordGroups: z.array(appliedFilterNodeSchema).optional(),
    records: z.array(appliedFilterNodeSchema).optional(),
    recordsExact: z.array(appliedFilterNodeSchema).optional(),
  })
  .optional();

const filtersSchema = requestFiltersSchema;

/** Model selection fields shared across search, message, regenerate, etc. */
const modelFieldsSchema = {
  modelKey: z.string().min(1, { message: 'Model key is required' }).optional(),
  modelName: z
    .string()
    .min(1, { message: 'Model name is required' })
    .optional(),
  modelFriendlyName: z
    .string()
    .min(1, { message: 'Model friendly name is required' })
    .optional(),
  reasoningEffort: z.enum(REASONING_EFFORT_VALUES).optional(),
};

/** Execution-context fields shared by every stream/regenerate body schema. */
const contextFieldsSchema = {
  timezone: z
    .string()
    .min(1, { message: 'Timezone must be a non-empty string' })
    .optional(),
  currentTime: z
    .string()
    .check(
      datetimeWithOffsetCheck(
        'currentTime must be an ISO 8601 datetime string',
      ),
    )
    .optional(),
  tools: z.array(z.string().min(1)).optional(),
  // AG-UI is the only supported wire protocol (see
  // utils/agui.ts::resolveProtocol, which always resolves to `agui`
  // regardless of this field). Kept accepted-but-inert for callers still
  // sending it; any value other than `agui` is rejected rather than
  // silently downgrading a caller to a protocol that no longer exists.
  // Must be declared here or Zod's default unknown-key stripping removes it
  // from req.body before the controller can read it — the validation
  // middleware replaces req.body with the parsed result.
  protocol: z
    .enum(['agui'], messageForUnknownOption('Protocol must be one of: agui.'))
    .optional(),
  // Per-request agent capability toggles. Subject to the same declare-or-be-
  // stripped constraint as `protocol` above. Python treats a missing flag as
  // enabled, so partial objects are valid.
  agentCapabilities: z
    .object({
      internalSearch: z.boolean().optional(),
      webSearch: z.boolean().optional(),
      deepSearch: z.boolean().optional(),
    })
    .optional(),
  // Client-generated identifier for this run so a later `POST .../cancel
  // {runId}` (see `cancelRunBodySchema`) can target it. Optional — a caller
  // that never sends one just can't be cooperatively cancelled.
  runId: z.string().check(guidCheck('runId must be a valid UUID')).optional(),
  // The person's answer to a tool approval card (Python `tool_approvals.py`). Same
  // declare-or-be-stripped constraint as `protocol` above. The call it answers is
  // the one saved server-side; only its id, the decision and "don't ask again" travel here.
  toolApproval: z
    .object({
      approvalId: z.string().min(1).max(64),
      decision: z.enum(['allow', 'deny']),
      remember: z.boolean().optional(),
    })
    .optional(),
};

/**
 * Opt-in: Python streams CUSTOM `retrieval_context` frames naming the records
 * and blocks that reached the model, plus a `run_usage` frame — evaluation
 * clients read both. Must be declared or Zod's unknown-key stripping removes
 * it from req.body before the controller sees it. The two stream routes accept
 * it; regenerating an answer is not a measured run.
 */
const retrievalContextFieldsSchema = {
  includeRetrievalContext: z.boolean().optional(),
};

/**
 * How long each search waits for the keyword (grep) pattern match after its
 * semantic results: `short` caps the wait (Python `pattern_match_wait_budget`)
 * for callers that would rather answer sooner, such as agents calling chat
 * over MCP; `full`, or leaving it out, waits for all of it. Declared on every
 * chat body that runs a search, or Zod strips it.
 */
const keywordSearchFieldsSchema = {
  keywordSearchBudget: keywordSearchBudgetSchema.optional(),
};

/** Body of `POST .../cancel` — one schema for both the assistant and agent
 * cancel routes, matching Python's `CancelRunRequest`. */
export const cancelRunBodySchema = z.object({
  runId: z.string().check(guidCheck('runId must be a valid UUID')),
});

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

/** `?projectId=<id>|unassigned` — narrows a conversation list to one project or to unlinked sessions. */
const projectIdQuerySchema = z
  .string()
  .refine((value) => value === 'unassigned' || OBJECT_ID_REGEX.test(value), {
    message: "projectId must be a valid project ID or 'unassigned'",
  })
  .optional();

// ---------------------------------------------------------------------------
// Reusable param shapes
// ---------------------------------------------------------------------------

const conversationIdParam = { conversationId: objectId('conversation ID') };
const messageIdParam = { messageId: objectId('message ID') };
const agentKeyParam = {
  agentKey: z.string().min(1, { message: 'Agent key is required' }),
};
/** The agent authority's cap on a version's note (`app/agents/versions.py` NOTE_MAX_CHARS). */
const AGENT_VERSION_NOTE_MAX = 500;
const webSearchProviderParam = {
  provider: z
    .string()
    .trim()
    .min(1, { message: 'Provider is required' })
    .transform((value) => value.toLowerCase()),
};
const modelUsageParam = {
  model_key: z.string().trim().min(1, { message: 'Model key is required' }),
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
  // Origin metadata ('upload' | 'paste-text') — see IChatAttachmentRef.source.
  source: z.enum(['upload', 'paste-text']).optional(),
});

/** A chat message's text, the same limit on a new chat and on every follow-up. */
const chatQuerySchema = z
  .string(messageWhenMissing('Query is required'))
  .min(1, { message: 'Query is required' })
  .max(100000, {
    message: 'Query exceeds maximum length of 100000 characters',
  });

const enterpriseSearchCreateBodySchema = z.object({
  query: chatQuerySchema,
  recordIds: z.array(objectId('record ID')).optional(),
  filters: filtersSchema,
  appliedFilters: appliedFiltersSchema,
  attachments: z.array(attachmentRefSchema).optional(),
  chatMode: z.enum(PIPESHUB_CHAT_MODE).optional(),
  // Only honored when creating a *new* conversation — see resolveProjectLink
  // (enterprise_search/utils/project-context.ts). Ignored on follow-up turns.
  projectId: objectId('project ID').optional(),
  projectVisibility: z.enum(['private', 'project']).optional(),
  ...modelFieldsSchema,
  ...contextFieldsSchema,
  ...retrievalContextFieldsSchema,
  ...keywordSearchFieldsSchema,
});

/** Opt-in: this turn is also being answered by a second model (Compare models); the stream announces the question's id early. */
const compareTurnSchema = z.boolean().optional();

export const enterpriseSearchCreateSchema = z.object({
  body: enterpriseSearchCreateBodySchema,
});

/** The assistant's chat modes on its public routes. */
export const UNIVERSAL_STREAM_CHAT_MODES = [
  'agent',
  'internal_search',
  'web_search',
] as const;

/**
 * The one definition of `chatMode` for the assistant's public chat routes
 * (QA6-F5): a new chat, a follow-up and a regenerate refuse the same values.
 * The stream routes require a mode; a regenerate may leave it out.
 */
export const assistantChatModeSchema = z.enum(UNIVERSAL_STREAM_CHAT_MODES, {
  error: 'Invalid chat mode',
});

/**
 * Which harness a chat turn asks for. `durable`: the assistant's Agent mode
 * asks for the harness (ASSIST1). `legacy`: the chat switch asks for the
 * classic path (CTL-7). The server decides either way.
 */
export const assistantHarnessSchema = z.enum(['durable', 'legacy']).optional();

/** What a stored assistant conversation reads as when its mode is one no chat route accepts any more. */
export const DEFAULT_ASSISTANT_CHAT_MODE: (typeof UNIVERSAL_STREAM_CHAT_MODES)[number] =
  'internal_search';

const ACCEPTED_SOMEWHERE: ReadonlySet<string> = new Set<string>([
  ...UNIVERSAL_STREAM_CHAT_MODES,
  ...Object.values(PIPESHUB_CHAT_MODE),
]);

/**
 * A stored assistant conversation's mode as the server gives it to a client
 * (QA6-F5), decided here only. A mode some chat route still accepts is served
 * as stored, an `agent:<strategy>` of an older client as `agent`, and
 * anything else (a retired mode such as `image`) as the default, so the
 * client's next request is one the routes take.
 */
export const servedAssistantChatMode = (stored: string): string => {
  if (ACCEPTED_SOMEWHERE.has(stored)) return stored;
  return stored.startsWith('agent:') ? 'agent' : DEFAULT_ASSISTANT_CHAT_MODE;
};

/** `conversation` with its stored mode as served; one that stores no mode is unchanged. */
export const withServedAssistantMode = <
  T extends { modelInfo?: unknown } | null | undefined,
>(
  conversation: T,
): T => {
  const modelInfo = conversation?.modelInfo as
    { chatMode?: unknown } | null | undefined;
  const stored = modelInfo?.chatMode;
  if (typeof stored !== 'string' || stored === '') return conversation;
  return {
    ...conversation,
    modelInfo: { ...modelInfo, chatMode: servedAssistantChatMode(stored) },
  };
};

/**
 * The coding engine for the conversation a turn creates (CMDUI2): an engine kind or `none`, in
 * `PUT …/coding-engine`'s format. The agent harness checks it as that route does; only create routes take it.
 */
export const newChatCodingEngineSchema = z
  .string()
  .regex(/^[a-z0-9][a-z0-9_.-]{0,63}$/, "Engine isn't in the right format")
  .optional();

/**
 * GIT-FLOW (WP-26): the GitHub repository for the conversation a turn creates (the composer chip), in
 * `PUT …/repository`'s format. The agent harness checks it as that route does; only create routes take it.
 */
export const newChatRepositorySchema = sessionRepositorySchema.optional();

/**
 * G-03..G-05: the run gates a turn asks for (a dry run, plan approval, a verify gate). Only the agent harness
 * enforces them; a gated turn it does not serve is refused, never run on today's loop.
 */
export const turnGatesSchema = contract(runGatesSchema).optional();

export const enterpriseSearchStreamCreateSchema = z.object({
  body: enterpriseSearchCreateBodySchema.extend({
    chatMode: assistantChatModeSchema,
    compareTurn: compareTurnSchema,
    harness: assistantHarnessSchema,
    gates: turnGatesSchema,
    codingEngine: newChatCodingEngineSchema,
    repository: newChatRepositorySchema,
  }),
});

/**
 * The Slack bot's own fields on the assistant's internal chat routes, which the agent routes take unvalidated: who is
 * behind its service credential (the caller context), the thread the turn came from (ADR-OWD-09 D-SLACK-ASK), and the
 * Agent mode's request for the agent harness. Without them the validation dropped each one before the route read it.
 */
const slackTurnFieldsSchema = {
  callerDisplayName: z.string().optional(),
  callerEmail: z.string().optional(),
  slackThread: z
    .object({ channel: z.string(), threadTs: z.string() })
    .optional(),
  harness: assistantHarnessSchema,
};

export const slackStreamCreateSchema = z.object({
  body: enterpriseSearchCreateBodySchema.extend(slackTurnFieldsSchema),
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

/**
 * POST /conversations/:id/share. `accessLevel` is part of the contract (OWD-19: `write` makes a collaborator who
 * sends their own turns); the body is strict so a field the route does not take is refused, never silently dropped
 * (R38AUTHZ-FU-1: a non-strict object stripped `accessLevel`, so every write share was stored as read).
 */
export const conversationShareParamsSchema = conversationIdParamsSchema.extend({
  body: z
    .object({
      userIds: userIdsSchema,
      accessLevel: z.enum(['read', 'write']).optional(),
    })
    .strict(),
});

/** POST /conversations/:id/unshare: only who to remove; any other field changes nothing, so it is dropped. */
export const conversationUnshareParamsSchema =
  conversationIdParamsSchema.extend({
    body: z.object({ userIds: userIdsSchema }),
  });

/** "Continue privately" (SS3): one fork per key; a repeat answers the same copy. */
const forkBodySchema = z
  .object({
    idempotencyKey: z
      .string()
      .regex(
        /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/,
        'idempotencyKey must be 1-128 letters, digits, _ . : -',
      ),
  })
  .strict();

/** Schema for POST /:conversationId/fork. */
export const conversationForkSchema = conversationIdParamsSchema.extend({
  body: forkBodySchema,
});

/** Schema for POST /:agentKey/conversations/:conversationId/fork. */
export const agentConversationForkSchema = z.object({
  params: z.object({ ...agentKeyParam, ...conversationIdParam }),
  body: forkBodySchema,
});

/** Schema for POST /:conversationId/cancel — cooperatively stop an in-flight assistant run. */
export const cancelConversationStreamParamsSchema =
  conversationIdParamsSchema.extend({
    body: cancelRunBodySchema,
  });

/** `projectId: null` unlinks — see `setConversationProject`, es_controller.ts. */
export const conversationProjectLinkSchema = conversationIdParamsSchema.extend({
  body: z.object({
    projectId: z
      .union([objectId('project ID'), z.null()])
      .refine((v) => v !== undefined, { message: 'projectId is required' }),
  }),
});

export const conversationProjectVisibilitySchema =
  conversationIdParamsSchema.extend({
    body: z.object({
      visibility: z.enum(['private', 'project']),
    }),
  });

export const agentConversationProjectLinkSchema = z.object({
  params: z.object({ ...agentKeyParam, ...conversationIdParam }),
  body: z.object({
    projectId: z
      .union([objectId('project ID'), z.null()])
      .refine((v) => v !== undefined, { message: 'projectId is required' }),
  }),
});

export const agentConversationProjectVisibilitySchema = z.object({
  params: z.object({ ...agentKeyParam, ...conversationIdParam }),
  body: z.object({
    visibility: z.enum(['private', 'project']),
  }),
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
export const deleteAgentConversationParamsSchema =
  agentConversationParamsSchema;

export const agentConversationTitleParamsSchema =
  agentConversationParamsSchema.extend({
    body: titleBodySchema,
  });

/** Most people one request may share an agent conversation with; each id costs an IAM lookup. */
const AGENT_SHARE_MAX_USERS = 50;

/**
 * Schema for POST /:agentKey/conversations/:conversationId/share — agent
 * conversations are read-only for recipients, so the only access level a caller
 * may name is 'read'. The strict body rejects `accessLevel: 'write'` outright
 * instead of silently stripping it.
 */
export const agentConversationShareParamsSchema =
  agentConversationParamsSchema.extend({
    body: z
      .object({
        userIds: userIdsSchema.max(AGENT_SHARE_MAX_USERS, {
          message: `At most ${AGENT_SHARE_MAX_USERS} users can be shared with at once`,
        }),
        accessLevel: z.literal('read').optional(),
      })
      .strict(),
  });

/** Schema for POST /:agentKey/conversations/:conversationId/unshare. */
export const agentConversationUnshareParamsSchema =
  agentConversationParamsSchema.extend({
    body: z
      .object({
        userIds: userIdsSchema.max(AGENT_SHARE_MAX_USERS, {
          message: `At most ${AGENT_SHARE_MAX_USERS} users can be unshared at once`,
        }),
      })
      .strict(),
  });

/** Schema for POST /:agentKey/conversations/:conversationId/cancel — cooperatively stop an in-flight agent run. */
export const cancelAgentConversationStreamParamsSchema =
  agentConversationParamsSchema.extend({
    body: cancelRunBodySchema,
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
      .preprocess(
        (value) => (value === '' ? undefined : value),
        z.string().optional(),
      )
      .refine(
        (value) => !value || !isNaN(new Date(value).getTime()),
        'Invalid start date format',
      ),
    endDate: z
      .preprocess(
        (value) => (value === '' ? undefined : value),
        z.string().optional(),
      )
      .refine(
        (value) => !value || !isNaN(new Date(value).getTime()),
        'Invalid end date format',
      ),
    messageType: z
      .preprocess(
        (value) => (value === '' ? undefined : value),
        z.string().optional(),
      )
      .refine(
        (value) =>
          !value ||
          [
            'user_query',
            'bot_response',
            'error',
            'feedback',
            'system',
          ].includes(value),
        'Invalid message type. Must be one of: user_query, bot_response, error, feedback, system',
      ),
  }),
});

// ---------------------------------------------------------------------------
// Add message
// ---------------------------------------------------------------------------

const addMessageBodySchema = z.object({
  query: chatQuerySchema,
  filters: filtersSchema,
  appliedFilters: appliedFiltersSchema,
  attachments: z.array(attachmentRefSchema).optional(),
  chatMode: z.enum(PIPESHUB_CHAT_MODE).optional(),
  ...modelFieldsSchema,
  ...contextFieldsSchema,
  ...retrievalContextFieldsSchema,
  ...keywordSearchFieldsSchema,
});

export const addMessageParamsSchema = z.object({
  params: z.object(conversationIdParam),
  body: addMessageBodySchema,
});

export const slackAddMessageStreamSchema = z.object({
  params: z.object(conversationIdParam),
  body: addMessageBodySchema.extend(slackTurnFieldsSchema),
});

export const addMessageStreamParamsSchema = z.object({
  params: z.object(conversationIdParam),
  body: addMessageBodySchema.extend({
    chatMode: assistantChatModeSchema,
    compareTurn: compareTurnSchema,
    harness: assistantHarnessSchema,
    gates: turnGatesSchema,
  }),
});

/**
 * An agent's chat modes on its conversation routes (LAT1: every mode on the
 * existing routes). The server routes each: the loop modes run durably,
 * planExecute (and its old name `verification`) on today's loop.
 */
export const AGENT_CHAT_MODES = [
  'quick',
  'auto',
  'react',
  'deep',
  'planExecute',
  'verification',
] as const;

const agentChatModeSchema = z.enum(AGENT_CHAT_MODES, {
  error: 'Invalid chat mode',
});

const agentAddMessageBodySchema = addMessageBodySchema.extend({
  chatMode: agentChatModeSchema,
  harness: assistantHarnessSchema,
  gates: turnGatesSchema,
});

const agentStreamCreateBodySchema = enterpriseSearchCreateBodySchema.extend({
  chatMode: agentChatModeSchema,
  harness: assistantHarnessSchema,
  gates: turnGatesSchema,
  codingEngine: newChatCodingEngineSchema,
  repository: newChatRepositorySchema,
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
// Agent non-streaming: create + add message (`chatMode` defaults to `quick`)
// ---------------------------------------------------------------------------

export const agentCreateConversationSchema = z.object({
  params: z.object(agentKeyParam),
  body: agentStreamCreateBodySchema.extend({
    chatMode: agentChatModeSchema.optional(),
  }),
});

export const agentAddMessageSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
  }),
  body: agentAddMessageBodySchema.extend({
    chatMode: agentChatModeSchema.optional(),
  }),
});

// ---------------------------------------------------------------------------
// Agent create (POST /agents/create) — gateway guardrails; Python enforces semantics
// ---------------------------------------------------------------------------

/** Defensive max length for long agent text fields (not enforced by Python). */
const agentLongTextSchema = z.string().max(100_000);

const agentToolRefSchema = z.object({
  name: z.string().trim().min(1),
  fullName: z.string().trim().min(1).optional(),
  description: z.string().max(10_000).optional(),
});

/**
 * The listed toolsets of the release catalog (generated from the Python registry,
 * C-29 keeps it current); OpenAPI `AgentCreateToolsetName` lists the same names.
 */
export const AGENT_CREATE_TOOLSET_NAMES: readonly string[] = listedReleaseNames(
  createExtensionRegistry(),
  'toolset',
);

const agentToolsetNameSchema = z.enum(
  AGENT_CREATE_TOOLSET_NAMES as [string, ...string[]],
  { error: 'Invalid toolset name' },
);

const agentToolsetSchema = z.object({
  name: z.string().trim().pipe(agentToolsetNameSchema),
  displayName: z.string().max(200).optional(),
  type: z.string().max(100).optional(),
  instanceId: z.string().max(256).optional(),
  instanceName: z.string().max(200).optional(),
  tools: z.array(agentToolRefSchema).optional(),
});

/**
 * One attached MCP server instance reference (see Python's `_parse_mcp_servers`,
 * `api/routes/agent.py`) — no secrets, just enough to resolve the instance +
 * an optional stored tool selection at chat time.
 */
const agentMcpServerSchema = z.object({
  instanceId: z
    .string()
    .trim()
    .min(1, { message: 'MCP server instanceId is required' })
    .max(256),
  name: z
    .string()
    .trim()
    .min(1, { message: 'MCP server name is required' })
    .max(200),
  displayName: z.string().max(200).optional(),
  typeId: z.string().max(200).optional(),
  /** Every tool the server offers at chat time, including ones it adds later. */
  allTools: z.boolean().optional(),
  // A loose bound on payload size only: Python allows 500 chosen tools and, with `allTools`,
  // keeps the first 500 of however many the server lists.
  tools: z.array(agentToolRefSchema).max(5000).optional(),
});

/**
 * `mcpServers` on create/update agent payloads. Rejects two attached
 * instances sharing the same `typeId`, mirroring Python's `_parse_mcp_servers`
 * duplicate-type rejection — instances with no `typeId` (custom/unregistered
 * servers) are exempt, same as the Python side.
 */
/**
 * API toolset operations an agent may call, each `"<toolsetId>:<operation>"`.
 * Python checks that every one exists in the organization.
 */
const agentApiToolsetToolsSchema = z
  .array(z.string().regex(/^[0-9a-f]{32}:[A-Za-z0-9_-]{1,64}$/))
  .max(500)
  .optional();

const agentMcpServersSchema = z
  .array(agentMcpServerSchema)
  .max(50)
  .optional()
  .superRefine((servers, ctx) => {
    if (!servers) {
      return;
    }
    const seenTypeIds = new Set<string>();
    servers.forEach((server, index) => {
      if (!server.typeId) {
        return;
      }
      if (seenTypeIds.has(server.typeId)) {
        ctx.addIssue({
          code: 'custom',
          message: `Duplicate MCP server type "${server.typeId}" — only one instance per server type is allowed`,
          path: [index, 'typeId'],
        });
        return;
      }
      seenTypeIds.add(server.typeId);
    });
  });

/** Most record groups, folders and records one knowledge source may be limited to. */
const MAX_AGENT_SOURCE_LIMIT_IDS = 200;
const AGENT_SOURCE_LIMIT_KEYS = ['recordGroups', 'records'] as const;

/**
 * A knowledge source's `filters`: an object (or the JSON text of one) whose
 * `recordGroups` and `records`, when given, limit the source to those nodes.
 * With neither the agent searches the whole source, so a value that cannot be
 * read is refused here: stored, it would count as "no limit". Other keys are
 * kept as sent. Older clients send `[]` or an empty string for "no filters".
 */
const readAgentKnowledgeFilters = (
  value: unknown,
  ctx: z.RefinementCtx,
): Record<string, unknown> => {
  let filters = value;
  if (typeof filters === 'string') {
    if (filters.trim() === '') return {};
    try {
      filters = JSON.parse(filters);
    } catch {
      filters = null;
    }
  }
  if (Array.isArray(filters) && filters.length === 0) return {};
  if (!filters || typeof filters !== 'object' || Array.isArray(filters)) {
    ctx.addIssue({
      code: 'custom',
      message:
        'Knowledge filters must be an object, with recordGroups and records as lists of ids',
    });
    return z.NEVER;
  }
  const limit = filters as Record<string, unknown>;
  let count = 0;
  for (const key of AGENT_SOURCE_LIMIT_KEYS) {
    const ids = limit[key];
    if (ids === undefined || ids === null) continue;
    if (
      !Array.isArray(ids) ||
      !ids.every((id) => appOrKbIdSchema.safeParse(id).success)
    ) {
      ctx.addIssue({
        code: 'custom',
        path: [key],
        message: `${key} must be a list of ids (UUIDs)`,
      });
      return z.NEVER;
    }
    count += ids.length;
  }
  if (count > MAX_AGENT_SOURCE_LIMIT_IDS) {
    ctx.addIssue({
      code: 'custom',
      message: `A knowledge source may be limited to at most ${MAX_AGENT_SOURCE_LIMIT_IDS} ids`,
    });
    return z.NEVER;
  }
  return limit;
};

const agentKnowledgeSchema = z.object({
  connectorId: z.string().trim().min(1),
  filters: z.unknown().transform(readAgentKnowledgeFilters).optional(),
});

const agentSkillSchema = z
  .object({
    name: z
      .string()
      .min(1, { message: 'Skill name is required' })
      .max(64, { message: 'Skill name must be 64 characters or fewer' })
      .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, {
        message:
          'Skill name must be lowercase alphanumeric with single hyphens',
      }),
  })
  .strict();

const agentModelEntrySchema = z.union([
  z.string().trim().min(1),
  z.object({
    modelKey: z.string().trim().min(1),
    modelName: z.string().optional(),
    isReasoning: z.boolean().optional(),
    provider: z.string().optional(),
  }),
]);

const agentWebSearchSchema = z.union([
  z.string().trim().min(1),
  z.object({
    provider: z.string().trim().min(1),
    providerKey: z.string().max(256).optional(),
    providerLabel: z.string().max(200).optional(),
    iconPath: z.string().max(500).optional(),
  }),
]);

const AGENT_REASONING_MODEL_REQUIRED_MESSAGE =
  'At least one reasoning model is required. Please add a reasoning model to your configuration.';

const hasReasoningModel = (
  models: Array<z.infer<typeof agentModelEntrySchema>>,
): boolean =>
  models.some(
    (model) =>
      typeof model === 'object' &&
      model !== null &&
      'isReasoning' in model &&
      model.isReasoning === true,
  );

/**
 * Agent model list is optional: an agent with no models configured falls back
 * to the organization's default LLM at execution time. When models ARE
 * provided, at least one must be a reasoning model so reasoning-effort
 * settings behave predictably.
 */
const agentModelsOptionalSchema = z
  .array(agentModelEntrySchema)
  .superRefine((models, ctx) => {
    if (models.length > 0 && !hasReasoningModel(models)) {
      ctx.addIssue({
        code: 'custom',
        message: AGENT_REASONING_MODEL_REQUIRED_MESSAGE,
      });
    }
  });

/**
 * An engine agent's settings (AG1), passed to the agents module, which parses
 * the narrowing and checks it against the org's engine ceiling. Here only the
 * shape: an object or null, and an account id or null.
 */
const agentEngineFields = {
  engineProfile: z
    .union([z.null(), z.record(z.string(), z.unknown())])
    .optional(),
  modelAccountId: z.union([z.null(), z.string().min(1).max(300)]).optional(),
};

/**
 * The model each sub-agent role calls (MA1), passed to the agents module,
 * which checks every entry is a configured LLM. Here only the shape.
 */
const agentRoleModelFields = {
  roleModels: z
    .record(
      z.string().regex(ROLE_KEY, { message: 'Invalid role key' }),
      z
        .object({
          modelKey: z.string().min(1),
          modelName: z.string().min(1),
          provider: z.string().min(1),
        })
        .strict(),
    )
    .refine((roles) => Object.keys(roles).length <= MAX_ROLE_MODELS, {
      message: `At most ${String(MAX_ROLE_MODELS)} roles`,
    })
    .optional(),
};

/**
 * An agent's own run gates (G-03..G-05) and environment (C20.18), passed to the agents module, which parses each and
 * refuses what it does not accept (a secret-looking variable, an unknown gate). Here only the shape: an object, or `{}`
 * or null to clear a stored one. Without them listed here the body's parse dropped both, so a builder save that
 * named them stored neither.
 */
const agentHarnessFields = {
  gates: z.union([z.null(), z.record(z.string(), z.unknown())]).optional(),
  environment: z
    .union([z.null(), z.record(z.string(), z.unknown())])
    .optional(),
};

const createAgentBodySchema = z.object({
  name: z
    .string(messageWhenMissing('Name is required'))
    .trim()
    .min(1, { message: 'Name is required' })
    .max(200, { message: 'Name must be less than 200 characters' }),
  /**
   * Optional: an agent created without models uses the organization's
   * default LLM at chat time (see get_llm_for_chat fallback chain).
   */
  models: agentModelsOptionalSchema.optional().default([]),
  description: agentLongTextSchema.optional(),
  startMessage: agentLongTextSchema.optional(),
  systemPrompt: agentLongTextSchema.optional(),
  instructions: agentLongTextSchema.optional(),
  tags: z.array(z.string().max(100)).max(50).optional(),
  shareWithOrg: z.boolean().optional(),
  isServiceAccount: z.boolean().optional(),
  /**
   * The service account this agent searches knowledge as. Carries the
   * account's `userId`, which is what the service accounts API returns, so
   * the value a picker sends back is the one it was given. Only meaningful
   * on a service agent; the Python side refuses it otherwise.
   */
  serviceAccountUserId: z.string().trim().min(1).max(128).optional(),
  toolsets: z.array(agentToolsetSchema).max(100).optional(),
  mcpServers: agentMcpServersSchema,
  knowledge: z.array(agentKnowledgeSchema).max(100).optional(),
  skills: z.array(agentSkillSchema).max(100).optional(),
  webSearch: z.union([z.null(), agentWebSearchSchema]).optional(),
  /** Agent-level fallback applied when a chat request omits its own reasoningEffort. */
  defaultReasoningEffort: z
    .union([z.null(), z.enum(REASONING_EFFORT_VALUES)])
    .optional(),
  sendUserContext: z.boolean().optional(),
  ...agentEngineFields,
  ...agentRoleModelFields,
  ...agentHarnessFields,
});

export const createAgentSchema = z.object({
  body: createAgentBodySchema
    .extend({
      apiToolsetTools: agentApiToolsetToolsSchema,
    })
    // Only a service agent searches as a service account. Accepting the pair
    // without the flag would create an agent that quietly searches as its
    // creator — the wide identity — while the caller believed they had asked
    // for the narrow one. On update there is no equivalent check here, because
    // the agent may already be a service agent and the body need not say so;
    // that one is checked against the stored agent on the Python side.
    .refine(
      (body) =>
        body.serviceAccountUserId === undefined ||
        body.isServiceAccount === true,
      {
        message:
          'Only a service agent searches as a service account. Set isServiceAccount, or leave the binding out.',
        path: ['serviceAccountUserId'],
      },
    ),
});

// ---------------------------------------------------------------------------
// Agent update schema (PUT /:agentKey)
// ---------------------------------------------------------------------------

const updateAgentBodySchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, { message: 'Name is required' })
    .max(200, { message: 'Name must be less than 200 characters' })
    .optional(),
  /** Optional; when present, an empty array clears the agent's models
   * and reverts it to the organization default LLM. */
  models: agentModelsOptionalSchema.optional(),
  description: agentLongTextSchema.optional(),
  startMessage: agentLongTextSchema.optional(),
  systemPrompt: agentLongTextSchema.optional(),
  instructions: agentLongTextSchema.optional(),
  tags: z.array(z.string().max(100)).max(50).optional(),
  shareWithOrg: z.boolean().optional(),
  isServiceAccount: z.boolean().optional(),
  /**
   * The service account this agent searches knowledge as. Nullable, unlike
   * on create: sending `null` is how a binding is cleared and the agent goes
   * back to searching as its creator.
   */
  serviceAccountUserId: z
    .union([z.null(), z.string().trim().min(1).max(128)])
    .optional(),
  toolsets: z.array(agentToolsetSchema).max(100).optional(),
  mcpServers: agentMcpServersSchema,
  knowledge: z.array(agentKnowledgeSchema).max(100).optional(),
  skills: z.array(agentSkillSchema).max(100).optional(),
  webSearch: z.union([z.null(), agentWebSearchSchema]).optional(),
  defaultReasoningEffort: z
    .union([z.null(), z.enum(REASONING_EFFORT_VALUES)])
    .optional(),
  sendUserContext: z.boolean().optional(),
  ...agentEngineFields,
  ...agentRoleModelFields,
  ...agentHarnessFields,
  /** A save publishes the agent's next version unless this is false: then it stays a draft (OWD-14). */
  publish: z.boolean().optional(),
  /** What changed, kept on the version this save publishes. */
  versionNote: z.string().max(AGENT_VERSION_NOTE_MAX).optional(),
});

export const updateAgentSchema = z.object({
  params: z.object(agentKeyParam),
  body: updateAgentBodySchema.extend({
    apiToolsetTools: agentApiToolsetToolsSchema,
  }),
});

// ---------------------------------------------------------------------------
// Agent versions (OWD-14): list, read, publish the draft, roll back
// ---------------------------------------------------------------------------

const agentVersionParam = {
  ...agentKeyParam,
  version: z.coerce.number().int().min(1).max(2_147_483_647),
};
const agentVersionNoteBody = z
  .object({ note: z.string().max(AGENT_VERSION_NOTE_MAX).optional() })
  .strict();

export const listAgentVersionsSchema = z.object({
  params: z.object(agentKeyParam),
});

export const getAgentVersionSchema = z.object({
  params: z.object(agentVersionParam),
});

export const publishAgentVersionSchema = z.object({
  params: z.object(agentKeyParam),
  body: agentVersionNoteBody.optional(),
});

export const rollbackAgentVersionSchema = z.object({
  params: z.object(agentVersionParam),
  body: agentVersionNoteBody.optional(),
});

// ---------------------------------------------------------------------------
// Agent get / list query schemas
// ---------------------------------------------------------------------------

export const getAgentParamsSchema = z.object({
  params: z.object(agentKeyParam),
});

export const getWebSearchProviderUsageRequestSchema = z.object({
  params: z.object(webSearchProviderParam),
  query: z.object({}).strict(),
});

export const getModelUsageRequestSchema = z.object({
  params: z.object(modelUsageParam),
  query: z.object({}).strict(),
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
            code: 'custom',
            message: error.message,
          });
        }
      }),
    sort_by: z
      .string()
      .trim()
      .min(1)
      .max(100)
      .optional()
      .default('updatedAtTimestamp'),
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

/** The composer's own limit on files per message (CHAT_ATTACHMENT_MAX_FILES in the frontend). */
const REGENERATE_ATTACHMENTS_MAX = 10;

const regenerateBodySchema = z.object({
  // Present only when the user edited the question being regenerated.
  query: chatQuerySchema.optional(),
  // The edited question's files, replacing the stored ones; omitted keeps them.
  attachments: z
    .array(attachmentRefSchema)
    .max(REGENERATE_ATTACHMENTS_MAX, {
      message: `At most ${REGENERATE_ATTACHMENTS_MAX} attachments per message`,
    })
    .optional(),
  filters: filtersSchema,
  // New answers to the question card this reply followed: regenerating then re-runs the turn with them.
  answers: z
    .string()
    .max(8000, { message: 'Answers are too long' })
    .refine((value) => value.trimStart().startsWith('User selections:'), {
      message: 'Answers must be the card answers ("User selections: ...")',
    })
    .optional(),
  chatMode: assistantChatModeSchema.optional(),
  harness: assistantHarnessSchema,
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

/** Schema for POST /:conversationId/message/:messageId/fork */
export const forkConversationParamsSchema = z.object({
  params: z.object({
    ...conversationIdParam,
    ...messageIdParam,
  }),
});

/** Schema for POST /:agentKey/conversations/:conversationId/message/:messageId/fork */
export const forkAgentConversationParamsSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
    ...messageIdParam,
  }),
});

export const regenerateAgentAnswersParamsSchema = z.object({
  params: z.object({
    ...agentKeyParam,
    ...conversationIdParam,
    ...messageIdParam,
  }),
  body: regenerateBodySchema.extend({
    chatMode: agentChatModeSchema,
  }),
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
      .string(messageWhenMissing('Search query is required'))
      .min(1, { message: 'Search query is required' }),
    page: pageSchema,
    limit: limitSchema,
    sortBy: z.enum(['createdAt', 'title']).optional(),
    sortOrder: z.enum(['asc', 'desc']).optional(),
  }),
});

/** "YYYY-MM-DD" or an ISO 8601 datetime with an offset; checked in full by the AI service. */
const searchDateSchema = z
  .string()
  .trim()
  .min(1, { message: 'A date filter cannot be empty' })
  .max(64, { message: 'A date filter must be at most 64 characters' })
  .optional();

export const enterpriseSearchSearchSchema = z.object({
  body: z.object({
    // Same ceiling as a chat question (chatQuerySchema). The XSS middleware
    // also refuses a longer query before it gets here.
    query: z
      .string()
      .min(1, { message: 'Search query is required' })
      .max(100000, {
        message: 'Search query exceeds maximum length of 100000 characters',
      }),
    filters: filtersSchema,
    // Default 10, as the OpenAPI spec says; `.optional()` would skip the default.
    limit: limitSchema,
    // Optional bounds on the source's own created / last-updated date. The AI
    // service parses them (the assistant's search tool uses the same parser)
    // and answers 400 with the reason when one is not a date it accepts.
    createdAfter: searchDateSchema,
    createdBefore: searchDateSchema,
    modifiedAfter: searchDateSchema,
    modifiedBefore: searchDateSchema,
    // false searches the query exactly as sent, with no model rewrite or
    // expanded variants: for callers that write their own queries, such as
    // an agent. Left out, the AI service rewrites and expands as before.
    rewriteQuery: z.boolean().optional(),
    // Set, the AI service searches the span the matching date bounds give
    // period by period, as the assistant's timeline tool does, and returns
    // the matches in date order, each labelled with its period. The value is
    // the date records are placed by. Left out, bounds on one date over more
    // than a month run as a timeline on it; 'off' is always a plain ranked search.
    timeline: z.enum(['created', 'modified', 'off']).optional(),
    // false runs the search without saving it to the caller's search history;
    // the response then has no `searchId`.
    saveHistory: z.boolean().optional(),
  }),
});

/** POST /api/v1/retrieve: chunks with citations, for a developer's own RAG. */
export const enterpriseSearchRetrieveSchema = z.object({
  body: z.object({
    query: z.string().min(1, { message: 'Query is required' }).max(100000, {
      message: 'Query exceeds maximum length of 100000 characters',
    }),
    filters: filtersSchema,
    // Chunks, not records; the AI service defaults to 8.
    limit: z.number().int().min(1).max(50).optional(),
    createdAfter: searchDateSchema,
    createdBefore: searchDateSchema,
    modifiedAfter: searchDateSchema,
    modifiedBefore: searchDateSchema,
    maxChunksPerRecord: z.number().int().min(1).max(50).optional(),
    rewriteQuery: z.boolean().optional(),
  }),
});

/**
 * Exact-text search. The pattern is literal text unless `regex` is true; the
 * AI service refuses what it cannot run (back-references, an invalid
 * expression) and answers 400 with the reason.
 */
export const enterpriseSearchGrepSchema = z.object({
  body: z.object({
    pattern: z
      .string()
      .min(1, { message: 'A pattern is required' })
      .max(500, { message: 'A pattern must be at most 500 characters' })
      .refine((value) => !/[\r\n\0]/.test(value), {
        message: 'A pattern must be one line',
      }),
    regex: z.boolean().optional(),
    caseSensitive: z.boolean().optional(),
    filters: filtersSchema,
    paths: z.array(z.string().min(1).max(256)).max(20).optional(),
    limit: z.number().int().min(1).max(100).optional(),
    contextLines: z.number().int().min(0).max(10).optional(),
    maxMatchesPerRecord: z.number().int().min(1).max(50).optional(),
    timeBudgetMs: z.number().int().min(500).max(30000).optional(),
  }),
});

const ENTITY_TYPES = [
  'department',
  'category',
  'subcategory',
  'topic',
  'language',
  'record_group',
  'record',
  'named_entity',
] as const;

export const enterpriseSearchEntitiesSchema = z.object({
  body: z.object({
    query: z.string().min(1, { message: 'A query is required' }).max(500),
    entityTypes: z.array(z.enum(ENTITY_TYPES)).max(10).optional(),
    namedEntityKinds: z.array(z.string().min(1).max(64)).max(10).optional(),
    limit: z.number().int().min(1).max(25).optional(),
  }),
});

export const enterpriseSearchEntityNeighborsSchema = z.object({
  body: z.object({
    entityId: z.string().min(1).max(256),
    entityType: z.enum(ENTITY_TYPES),
    recordTypes: z.array(z.string().min(1).max(64)).max(20).optional(),
    recordLimit: z.number().int().min(1).max(50).optional(),
    relatedLimit: z.number().int().min(1).max(50).optional(),
    cursor: z.string().max(64).optional(),
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
    startDate: z.string().check(datetimeWithOffsetCheck()).optional(),
    endDate: z.string().check(datetimeWithOffsetCheck()).optional(),
    shared: z.enum(['true', 'false', '1', '0']).optional(),
    projectId: projectIdQuerySchema,
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
    projectId: projectIdQuerySchema,
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
            code: 'custom',
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
  query: z
    .object({
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
          value === 'createdAt' ||
          value === 'lastActivityAt' ||
          value === 'title'
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
        .max(1000, {
          message: 'Search parameter too long (max 1000 characters)',
        })
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
              code: 'custom',
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
    })
    .strict(),
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
    startDate: z.string().check(datetimeWithOffsetCheck()).optional(),
    endDate: z.string().check(datetimeWithOffsetCheck()).optional(),
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
};

const attachmentUploadBodySchema = z.object({
  conversationId: z
    .preprocess(
      (v) => (typeof v === 'string' ? v.trim() : v),
      z.union([
        z.literal(''),
        z.string().regex(OBJECT_ID_REGEX, {
          message: 'Invalid conversation ID format',
        }),
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

// ---------------------------------------------------------------------------
// Compare models: a hidden branch conversation answers the same question
// ---------------------------------------------------------------------------

/** `compare/branch` body: the saved question to re-ask in the branch. */
export const compareBranchParamsSchema = z.object({
  params: z.object(conversationIdParam),
  body: z.object({ userMessageId: objectId('message ID') }),
});

export const compareListParamsSchema = z.object({
  params: z.object(conversationIdParam),
});

/** `compare/choose` body: `primary` keeps the conversation's own answer, a branch id swaps that branch's turn in. */
export const compareChooseParamsSchema = z.object({
  params: z.object(conversationIdParam),
  body: z.object({
    choice: z.string().regex(/^(primary|[a-f0-9]{24})$/i, {
      message: "choice must be 'primary' or a branch conversation ID",
    }),
  }),
});
