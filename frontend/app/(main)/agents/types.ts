import type {
  Conversation,
  ConversationMessage,
  ConversationPagination,
  ModelInfo,
  SharedWithEntry,
  ConversationsListResponse,
} from '@/chat/types';

/**
 * GET /api/v1/agents — pagination envelope (`pagination` object).
 */
export interface AgentsListPagination {
  currentPage: number;
  limit: number;
  totalItems: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

/**
 * Raw API envelope for GET /api/v1/agents.
 */
export interface AgentsListApiResponse {
  success: boolean;
  agents: AgentListRecord[];
  pagination: AgentsListPagination;
}

/**
 * One row in the `agents` array from GET /api/v1/agents.
 * Several fields are optional because the backend omits them on some records.
 */
export interface AgentListRecord {
  /** Business id (UUID) — use for routes and API paths */
  id: string;
  /** Arango-style document key (matches `id` in practice) */
  _key: string;
  /** Collection-qualified id, e.g. `agentInstances/<uuid>` */
  _id: string;
  name: string;
  description: string;
  models: string[];
  startMessage: string;
  systemPrompt: string;
  tags: string[];
  isActive: boolean;
  isDeleted: boolean;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  /** MongoDB user ID of the agent creator */
  createdBy: string;
  updatedBy?: string;
  /** Omitted on some agents */
  instructions?: string;
  /** Omitted on some agents (e.g. legacy rows) */
  isServiceAccount?: boolean;
  /** e.g. `INDIVIDUAL` */
  access_type: string;
  /** e.g. `OWNER` */
  user_role: string;
  can_edit: boolean;
  can_delete: boolean;
  can_share: boolean;
  can_view: boolean;
  shareWithOrg: boolean;
}

export interface AgentsListParams {
  page?: number;
  limit?: number;
  search?: string;
  sort?: string;
  order?: 'asc' | 'desc';
}

// ── GET /api/v1/agents/:id (single agent) ─────────────────────────

/**
 * Raw API envelope for GET /api/v1/agents/:agentId.
 * Fields optional where error or legacy payloads omit them.
 */
export interface GetAgentApiResponse {
  status?: string;
  message?: string;
  agent?: AgentDetail;
}

/** LLM model attached to an agent (detail response — not the list `models: string[]`). */
export interface AgentConfiguredModel {
  modelKey: string;
  modelName: string;
  provider: string;
  isReasoning: boolean;
  isMultimodal: boolean;
  isDefault: boolean;
  modelType: string;
  modelFriendlyName: string;
}

/** Tool definition nested under `toolsets[].tools[]`. */
export interface AgentToolDefinition {
  _key: string;
  description: string;
  fullName: string;
  name: string;
  toolsetName: string;
  /** True when the tool's `@tool` has been removed from server code since the agent was last saved. */
  deprecated?: boolean;
}

/** Toolset grouping (connector / app tools) on a single agent. */
export interface AgentToolset {
  _key: string;
  displayName: string;
  /** Optional per-instance label (e.g. sidebar instance name) — distinct from integration `name`. */
  instanceName?: string;
  instanceId: string;
  name: string;
  /** Optional branded icon URL/path from the API (same convention as agent builder). */
  iconPath?: string;
  selectedTools: unknown[] | null;
  tools: AgentToolDefinition[];
  type: string;
}

/**
 * `agent` object from GET /api/v1/agents/:id (success payload).
 * Optional fields are omitted by some API versions or edge records.
 */
export interface AgentDetail {
  models: AgentConfiguredModel[];
  /** Often `""` when unset */
  instructions?: string;
  startMessage: string;
  updatedBy?: string;
  description: string;
  updatedAtTimestamp: number;
  isActive: boolean;
  tags: string[];
  systemPrompt: string;
  createdAtTimestamp: number;
  isDeleted: boolean;
  /** MongoDB user ID of the agent creator */
  createdBy: string;
  name: string;
  id: string;
  isServiceAccount?: boolean;
  _key: string;
  _id: string;
  toolsets: AgentToolset[];
  knowledge: unknown[];
  /** Optional web-search provider attached to this agent. */
  webSearch?: {
    provider: string;
    providerKey: string;
    providerLabel?: string;
    iconPath?: string;
  } | null;
  shareWithOrg: boolean;
  access_type: string;
  user_role: string;
  can_edit: boolean;
  can_delete: boolean;
  can_share: boolean;
  can_view: boolean;
}

// ── Builder catalog rows (tool list + KB) ───────────────────────

/** One tool row built from toolsets or merged from saved agent detail. */
export interface AgentToolsListRow {
  tool_id: string;
  app_name: string;
  tool_name: string;
  full_name: string;
  description: string;
  parameters?: unknown[];
}

/** KB collection row for agent builder palette. */
export interface KnowledgeBaseForBuilder {
  id: string;
  name: string;
  connectorId: string;
}

// ── Knowledge Hub App Nodes (for agent builder apps palette) ─────

/** Single node item from GET /api/v1/knowledgeBase/knowledge-hub/nodes */
export interface KnowledgeHubAppNode {
  id: string;
  name: string;
  nodeType: string;
  parentId: string | null;
  origin: string;
  connector: string | null;
  recordType: string | null;
  recordGroupType: string | null;
  indexingStatus: string | null;
  reason: string | null;
  createdAt: number;
  updatedAt: number;
  sizeInBytes: number | null;
  mimeType: string | null;
  extension: string | null;
  webUrl: string | null;
  hasChildren: boolean;
  previewRenderable: boolean | null;
  permission: {
    role: string;
    canEdit: boolean;
    canDelete: boolean;
  } | null;
  sharingStatus: string | null;
  isInternal: boolean;
}

/** Pagination envelope from knowledge-hub nodes API */
export interface KnowledgeHubNodesPagination {
  page: number;
  limit: number;
  totalItems: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

/** Full response from GET /api/v1/knowledgeBase/knowledge-hub/nodes */
export interface KnowledgeHubNodesApiResponse {
  success: boolean;
  error: string | null;
  id: string | null;
  currentNode: { id: string; name: string; nodeType: string; subType?: string | null } | null;
  parentNode: { id: string; name: string; nodeType: string; subType?: string | null } | null;
  items: KnowledgeHubAppNode[];
  pagination: KnowledgeHubNodesPagination;
  filters: {
    applied: {
      q: string | null;
      nodeTypes: string[] | null;
      recordTypes: string[] | null;
      origins: string[] | null;
      connectorIds: string[] | null;
      indexingStatus: string[] | null;
      createdAt: unknown;
      updatedAt: unknown;
      size: unknown;
      sortBy: string;
      sortOrder: string;
    };
    available: unknown;
  };
  breadcrumbs: unknown;
  counts: unknown;
  permissions: unknown;
}

// ── Client return shapes (AgentsApi) ─────────────────────────────

/** Normalized result of {@link AgentsApi.getAgents}. */
export interface AgentsListResult {
  agents: AgentListRecord[];
  pagination: AgentsListPagination;
}

/** Normalized result of {@link AgentsApi.getAgent}. */
export interface GetAgentResult {
  agent: AgentDetail | null;
  toolFullNames: string[];
}

/** Normalized result of {@link AgentsApi.getKnowledgeBasesForBuilder}. */
export interface KnowledgeBasesForBuilderResult {
  knowledgeBases: KnowledgeBaseForBuilder[];
}

/** POST /api/v1/agents/create — response envelope. */
export interface CreateAgentApiResponse {
  agent?: AgentDetail;
}

/**
 * PUT /api/v1/agents/:agentKey — some deployments return only `status` / `message` on success.
 */
export interface UpdateAgentApiResponse {
  agent?: AgentDetail;
  status?: string;
  message?: string;
}

/** GET /api/v1/knowledgeBase/ — list envelope (builder). */
export interface KnowledgeBaseListApiResponse {
  knowledgeBases?: KnowledgeBaseForBuilder[];
}

// ── Agent-scoped conversations (same routes under /agents/:id) ─

/**
 * Conversation payload embedded in
 * GET /api/v1/agents/:agentId/conversations/:conversationId.
 */
export interface AgentConversationDetailApi {
  id: string;
  title: string;
  initiator: string;
  createdAt?: string;
  isShared: boolean;
  sharedWith: SharedWithEntry[];
  status: string;
  messages: ConversationMessage[];
  modelInfo: ModelInfo;
  access: { isOwner: boolean; accessLevel: string };
  /** Optional: present when the API returns paginated messages. */
  pagination?: ConversationPagination;
}

export interface AgentConversationDetailApiResponse {
  conversation?: AgentConversationDetailApi;
}

/** Normalized result of {@link AgentsApi.fetchAgentConversation}. */
export interface FetchAgentConversationResult {
  conversation: AgentConversationDetailApi;
  messages: ConversationMessage[];
  pagination: ConversationPagination;
}

/** Normalized result of {@link AgentsApi.fetchAgentConversations}. */
export interface AgentConversationsListResult {
  conversations: Conversation[];
  sharedConversations: Conversation[];
  pagination: ConversationsListResponse['pagination'];
}
