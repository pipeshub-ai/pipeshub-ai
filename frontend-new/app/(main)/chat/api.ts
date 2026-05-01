import { apiClient, streamSSERequest, SSEEvent } from '@/lib/api';
import {
  ChatMessage,
  Conversation,
  ConversationMessage,
  ConversationsListResponse,
  ConversationApiResponse,
  ConversationSource,
  ModelInfo,
  SharedWithEntry,
  StreamChatRequest,
  AgentStrategyApiSegment,
  SSEEventType,
  SSEConnectedEvent,
  SSEStatusEvent,
  SSEAnswerChunkEvent,
  SSECompleteEvent,
  SSEErrorEvent,
  SSEArtifactEvent,
  AvailableLlmModel,
  SearchRequest,
  SearchResponse,
  streamChatModeToAgentApiChatMode,
} from './types';
import { getClientTimezone, getClientCurrentTime } from './utils/client-time';

export interface FeedbackPayload {
  isHelpful: boolean;
  categories?: string[];
  comments?: {
    positive?: string;
    negative?: string;
    suggestions?: string;
  };
}

export interface StreamMessageCallbacks {
  onConnected?: (data: SSEConnectedEvent) => void;
  onStatus?: (data: SSEStatusEvent) => void;
  onChunk?: (data: SSEAnswerChunkEvent) => void;
  onComplete?: (data: SSECompleteEvent) => void;
  onArtifact?: (data: SSEArtifactEvent) => void;
  /** Backend is discarding partial output (citation verify / re-parse) — clear UI buffer */
  onRestreaming?: () => void;
  onError?: (error: Error) => void;
  signal?: AbortSignal;
}

/** Map GET /conversations (or agent conversations) row → sidebar `Conversation` */
export function mapApiConversationToConversation(conv: ConversationApiResponse): Conversation {
  return {
    id: conv._id,
    title: conv.title,
    createdAt: conv.createdAt,
    updatedAt: conv.updatedAt,
    isShared: conv.isShared,
    sharedWith: conv.sharedWith ?? [],
    lastActivityAt: conv.lastActivityAt,
    status: conv.status,
    modelInfo: conv.modelInfo,
    isOwner: conv.isOwner,
  };
}

const transformConversation = mapApiConversationToConversation;

export interface FetchConversationsOptions {
  /** 'owned' fetches the user's own chats; 'shared' fetches chats shared with them. */
  source: ConversationSource;
  /** Passed to GET /api/v1/conversations?search= — server matches title and message content */
  search?: string;
  signal?: AbortSignal;
}

/**
 * Normalise an app/KB filter list and return a `{ filters }` spread fragment.
 *
 * Filters out any non-string or blank IDs, then only returns the key when at
 * least one valid ID remains — so the backend never receives `filters: { apps:
 * [], kb: [] }` and applies an unintended "no scope" constraint.
 */
function buildFiltersPayload(
  apps: unknown[] | null | undefined,
  kb: unknown[] | null | undefined,
): Record<string, unknown> {
  const validApps = (apps ?? []).filter(
    (id): id is string => typeof id === 'string' && id.trim().length > 0,
  );
  const validKb = (kb ?? []).filter(
    (id): id is string => typeof id === 'string' && id.trim().length > 0,
  );
  return validApps.length > 0 || validKb.length > 0
    ? { filters: { apps: validApps, kb: validKb } }
    : {};
}

// Chat API endpoints
export const ChatApi = {
  // Fetch one page of conversations for a single source (owned or shared).
  // Call once per tab if you need both lists.
  async fetchConversations(
    page: number = 1,
    limit: number = 20,
    options: FetchConversationsOptions
  ): Promise<{
    conversations: Conversation[];
    source: ConversationSource;
    pagination: ConversationsListResponse['pagination'];
  }> {
    const search = options.search?.trim();
    const params: Record<string, string | number> = {
      page,
      limit,
      source: options.source,
    };
    if (search) {
      params.search = search;
    }

    const { data } = await apiClient.get<ConversationsListResponse>(
      `/api/v1/conversations`,
      { params, signal: options.signal }
    );

    return {
      conversations: data.conversations.map(transformConversation),
      source: data.source,
      pagination: data.pagination,
    };
  },

  /**
   * Fetch a specific conversation with all its messages.
   * Used when loading conversation history from sidebar click.
   *
   * Response is wrapped: { conversation: {...}, filters: {...}, meta: {...} }
   */
  async fetchConversation(conversationId: string): Promise<{
    conversation: {
      id: string;
      title: string;
      initiator: string;
      messages: ConversationMessage[];
      status: string;
      modelInfo: ModelInfo;
      isShared: boolean;
      sharedWith: SharedWithEntry[];
      access: {
        isOwner: boolean;
        accessLevel: string;
      };
    };
    messages: ConversationMessage[];
  }> {
    const { data } = await apiClient.get<{
      conversation: {
        id: string;
        title: string;
        initiator: string;
        createdAt: string;
        isShared: boolean;
        sharedWith: SharedWithEntry[];
        status: string;
        messages: ConversationMessage[];
        modelInfo: ModelInfo;
        pagination: {
          page: number;
          limit: number;
          totalCount: number;
          totalPages: number;
          hasNextPage: boolean;
          hasPrevPage: boolean;
        };
        access: {
          isOwner: boolean;
          accessLevel: string;
        };
      };
      filters: Record<string, unknown>;
      meta: Record<string, unknown>;
    }>(`/api/v1/conversations/${conversationId}/`);

    return {
      conversation: data.conversation,
      messages: data.conversation.messages || [],
    };
  },

  // Fetch messages for a conversation
  async fetchMessages(conversationId: string): Promise<ChatMessage[]> {
    const { data } = await apiClient.get<ChatMessage[]>(
      `/api/chat/conversations/${conversationId}/messages`
    );
    return data;
  },

  // Create a new conversation
  async createConversation(title: string): Promise<Conversation> {
    const { data } = await apiClient.post<Conversation>(
      `/api/chat/conversations`,
      { title }
    );
    return data;
  },

  /**
   * Upload a chat attachment via the existing storage API. Returns the storage
   * `documentId`, which is then forwarded to the conversation stream so the
   * Python query service can fetch + parse the file.
   *
   * The storage upload route accepts a single `file` field per request and a
   * matching `documentName` for the saved record (no extension, per
   * `validateFileAndDocumentName`). We split the original name into
   * `documentName` + `extension` before sending.
   */
  async uploadAttachment(
    file: File,
    options?: { signal?: AbortSignal }
  ): Promise<{ documentId: string; fileName: string; mimeType: string; sizeInBytes: number }> {
    const formData = new FormData();
    formData.append('file', file);
    // UploadNewSchema (POST /api/v1/document/upload) requires this multipart field.
    formData.append('isVersionedFile', 'false');

    const lastDot = file.name.lastIndexOf('.');
    const documentName = lastDot > 0 ? file.name.slice(0, lastDot) : file.name;
    if (documentName) {
      formData.append('documentName', documentName);
    }

    const { data } = await apiClient.post<{
      _id: string;
      documentName?: string;
      extension?: string;
      sizeInBytes?: number;
      mimeType?: string;
    }>(`/api/v1/document/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      signal: options?.signal,
    });

    if (!data?._id) {
      throw new Error('Upload succeeded but the server response did not include a documentId');
    }

    return {
      documentId: data._id,
      fileName: file.name,
      mimeType: data.mimeType || file.type || 'application/octet-stream',
      sizeInBytes: data.sizeInBytes ?? file.size,
    };
  },

  /**
   * Stream a chat message with SSE
   * Handles new conversation creation and existing conversation messages
   */
  async streamMessage(
    request: StreamChatRequest,
    callbacks: StreamMessageCallbacks
  ): Promise<void> {
    let endpoint: string;
    let payload: Record<string, unknown>;

    const attachmentDocumentIds = (request.attachmentDocumentIds ?? []).filter(
      (id): id is string => typeof id === 'string' && id.trim().length > 0,
    );

    if (request.agentId) {
      const agentChatMode = streamChatModeToAgentApiChatMode(request.chatMode);
      endpoint = request.conversationId
        ? `/api/v1/agents/${request.agentId}/conversations/${request.conversationId}/messages/stream`
        : `/api/v1/agents/${request.agentId}/conversations/stream`;
      const f = request.filters;
      payload = {
        query: request.query,
        modelKey: request.modelKey,
        modelName: request.modelName,
        modelFriendlyName: request.modelFriendlyName ?? request.modelName,
        chatMode: agentChatMode,
        timezone: getClientTimezone(),
        currentTime: getClientCurrentTime(),
        tools: [...(request.agentStreamTools ?? [])],
        ...buildFiltersPayload(f?.apps, f?.kb),
        ...(attachmentDocumentIds.length > 0 ? { attachmentDocumentIds } : {}),
      };
    } else {
      endpoint = request.conversationId
        ? `/api/v1/conversations/${request.conversationId}/messages/stream`
        : `/api/v1/conversations/stream`;
      // Rename `agentStreamTools` → `tools` (Node.js controller reads `req.body.tools`
      // uniformly for both agent and non-agent paths) and validate filters.
      const { agentStreamTools, filters: reqFilters, attachmentDocumentIds: _ignored, ...rest } = request;
      payload = {
        ...rest,
        ...(agentStreamTools !== undefined ? { tools: agentStreamTools } : {}),
        ...buildFiltersPayload(reqFilters?.apps, reqFilters?.kb),
        ...(attachmentDocumentIds.length > 0 ? { attachmentDocumentIds } : {}),
      };
    }

    // Track whether a complete event was received and the last SSE error
    let receivedComplete = false;
    let lastSSEError: SSEErrorEvent | null = null;

    await streamSSERequest(
      endpoint,
      payload,
      {
        onEvent: (event: SSEEvent) => {
          switch (event.event as SSEEventType) {
            case 'connected':
              callbacks.onConnected?.(event.data as SSEConnectedEvent);
              break;
            case 'status':
              callbacks.onStatus?.(event.data as SSEStatusEvent);
              break;
            case 'answer_chunk':
              callbacks.onChunk?.(event.data as SSEAnswerChunkEvent);
              break;
            case 'complete':
              receivedComplete = true;
              callbacks.onComplete?.(event.data as SSECompleteEvent);
              break;
            case 'restreaming':
              callbacks.onRestreaming?.();
              break;
            case 'tool_call':
            case 'tool_success':
            case 'tool_error':
            case 'tool_calls':
            case 'tool_result':
              // Tool / orchestration events — no separate UI; status + answer_chunk carry UX
              break;
            case 'metadata':
              // Citations / enrichment hints — UI uses answer_chunk + complete; ignore payload
              break;
            case 'artifact':
              callbacks.onArtifact?.(event.data as SSEArtifactEvent);
              break;
            case 'error':
              // SSE error events may be non-fatal — the backend might still
              // continue streaming after this. Save the error and check after
              // the stream ends whether a complete event followed.
              lastSSEError = event.data as SSEErrorEvent;
              console.warn('[Chat SSE] Backend warning:', lastSSEError.message || lastSSEError.error);
              break;
            default:
              // Future / proxy-only event names — ignore silently (no user-facing noise)
              break;
          }
        },
        onError: (error) => {
          callbacks.onError?.(error);
        },
        signal: callbacks.signal,
      }
    );

    // If the stream ended without a complete event but had an error,
    // the error was fatal — propagate it.
    if (!receivedComplete && lastSSEError) {
      const errorMessage = lastSSEError.message || lastSSEError.error || 'Stream ended with an error';
      callbacks.onError?.(new Error(errorMessage));
    }
  },

  /**
   * Regenerate the last bot response SSE stream.
   * Endpoint: POST /api/v1/conversations/:conversationId/message/:messageId/regenerate
   * Response: SSE stream with the same events as streamMessage.
   *
   * Pure transport: the caller is responsible for resolving the model and
   * mode/filters (typically from the slot being regenerated, not global UI
   * state) and passing them in, matching the pattern used by streamMessage
   * and streamAgentRegenerate.
   */
  async streamRegenerate(
    conversationId: string,
    messageId: string,
    callbacks: StreamMessageCallbacks,
    request: {
      modelKey: string;
      modelName: string;
      modelFriendlyName: string;
      chatMode: StreamChatRequest['chatMode'];
      filters: StreamChatRequest['filters'];
      /** Universal agent mode: explicit tool subset (null = all, [] = none). */
      agentStreamTools?: string[];
    }
  ): Promise<void> {
    const endpoint = `/api/v1/conversations/${conversationId}/message/${messageId}/regenerate`;

    let receivedComplete = false;
    let lastSSEError: SSEErrorEvent | null = null;

    const body: Record<string, unknown> = {
      modelKey: request.modelKey,
      modelName: request.modelName,
      modelFriendlyName: request.modelFriendlyName,
      chatMode: request.chatMode,
      ...buildFiltersPayload(request.filters?.apps, request.filters?.kb),
      timezone: getClientTimezone(),
      currentTime: getClientCurrentTime(),
    };
    if (request.agentStreamTools !== undefined) {
      body.tools = request.agentStreamTools;
    }

    await streamSSERequest(
      endpoint,
      body,
      {
        onEvent: (event: SSEEvent) => {
          switch (event.event as SSEEventType) {
            case 'connected':
              callbacks.onConnected?.(event.data as SSEConnectedEvent);
              break;
            case 'status':
              callbacks.onStatus?.(event.data as SSEStatusEvent);
              break;
            case 'answer_chunk':
              callbacks.onChunk?.(event.data as SSEAnswerChunkEvent);
              break;
            case 'complete':
              receivedComplete = true;
              callbacks.onComplete?.(event.data as SSECompleteEvent);
              break;
            case 'restreaming':
              callbacks.onRestreaming?.();
              break;
            case 'tool_call':
            case 'tool_success':
            case 'tool_error':
            case 'tool_calls':
            case 'tool_result':
              break;
            case 'metadata':
              break;
            case 'error':
              lastSSEError = event.data as SSEErrorEvent;
              console.warn('[Regenerate SSE] Backend warning:', lastSSEError.message || lastSSEError.error);
              break;
            default:
              break;
          }
        },
        onError: (error) => {
          callbacks.onError?.(error);
        },
        signal: callbacks.signal,
      }
    );

    if (!receivedComplete && lastSSEError) {
      const errorMessage = lastSSEError.message || lastSSEError.error || 'Stream ended with an error';
      callbacks.onError?.(new Error(errorMessage));
    }
  },

  /**
   * Regenerate a message in an agent conversation (SSE).
   * POST /api/v1/agents/:agentId/conversations/:conversationId/message/:messageId/regenerate
   */
  async streamAgentRegenerate(
    agentId: string,
    conversationId: string,
    messageId: string,
    callbacks: StreamMessageCallbacks,
    model: {
      modelKey: string;
      modelName: string;
      modelProvider: string;
      chatMode: AgentStrategyApiSegment;
      /** Explicit tool subset for this agent context (all tools when omitted). */
      tools?: string[];
      filters?: { apps: string[]; kb: string[] };
    }
  ): Promise<void> {
    const endpoint = `/api/v1/agents/${agentId}/conversations/${conversationId}/message/${messageId}/regenerate`;

    let receivedComplete = false;
    let lastSSEError: SSEErrorEvent | null = null;

    const agentRegenBody: Record<string, unknown> = {
      modelKey: model.modelKey,
      modelName: model.modelName,
      modelProvider: model.modelProvider,
      chatMode: model.chatMode,
      timezone: getClientTimezone(),
      currentTime: getClientCurrentTime(),
      ...(model.filters ? { filters: model.filters } : {}),
    };
    if (model.tools !== undefined) {
      agentRegenBody.tools = model.tools;
    }

    await streamSSERequest(
      endpoint,
      agentRegenBody,
      {
        onEvent: (event: SSEEvent) => {
          switch (event.event as SSEEventType) {
            case 'connected':
              callbacks.onConnected?.(event.data as SSEConnectedEvent);
              break;
            case 'status':
              callbacks.onStatus?.(event.data as SSEStatusEvent);
              break;
            case 'answer_chunk':
              callbacks.onChunk?.(event.data as SSEAnswerChunkEvent);
              break;
            case 'complete':
              receivedComplete = true;
              callbacks.onComplete?.(event.data as SSECompleteEvent);
              break;
            case 'restreaming':
              callbacks.onRestreaming?.();
              break;
            case 'tool_call':
            case 'tool_success':
            case 'tool_error':
            case 'tool_calls':
            case 'tool_result':
              break;
            case 'metadata':
              break;
            case 'error':
              lastSSEError = event.data as SSEErrorEvent;
              console.warn('[Agent regenerate SSE] Backend warning:', lastSSEError.message || lastSSEError.error);
              break;
            default:
              break;
          }
        },
        onError: (error) => {
          callbacks.onError?.(error);
        },
        signal: callbacks.signal,
      }
    );

    if (!receivedComplete && lastSSEError) {
      const errorMessage = lastSSEError.message || lastSSEError.error || 'Stream ended with an error';
      callbacks.onError?.(new Error(errorMessage));
    }
  },

  /**
   * Search across knowledge bases and connectors.
   * Endpoint: POST /api/v1/search
   */
  async search(request: SearchRequest, signal?: AbortSignal): Promise<SearchResponse> {
    const { data } = await apiClient.post<SearchResponse>(
      '/api/v1/search',
      request,
      {
        signal,
        // Search errors are shown inline on the chat page; suppress avoids duplicate
        // toasts and odd UX when a request is superseded by a newer search (abort).
        suppressErrorToast: true,
      }
    );
    return data;
  },

  /**
   * Submit feedback (thumbs up / thumbs down) for a bot response message.
   * Endpoint: POST /api/v1/conversations/:conversationId/message/:messageId/feedback
   */
  async submitFeedback(
    conversationId: string,
    messageId: string,
    feedback: FeedbackPayload
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/conversations/${conversationId}/message/${messageId}/feedback`,
      feedback
    );
  },

  /**
   * Submit feedback for a bot response in an agent conversation.
   * Endpoint: POST /api/v1/agents/:agentId/conversations/:conversationId/message/:messageId/feedback
   */
  async submitAgentFeedback(
    agentId: string,
    conversationId: string,
    messageId: string,
    feedback: FeedbackPayload
  ): Promise<void> {
    await apiClient.post(
      `/api/v1/agents/${agentId}/conversations/${conversationId}/message/${messageId}/feedback`,
      feedback
    );
  },

  // Rename a conversation
  async renameConversation(conversationId: string, title: string): Promise<void> {
    await apiClient.patch(`/api/v1/conversations/${conversationId}/title`, { title });
  },

  // Delete a conversation
  async deleteConversation(conversationId: string): Promise<void> {
    await apiClient.delete(`/api/v1/conversations/${conversationId}`);
  },

  // Archive a conversation
  async archiveConversation(conversationId: string): Promise<void> {
    await apiClient.patch(`/api/v1/conversations/${conversationId}/archive`);
  },

  // Restore (unarchive) a conversation
  async restoreConversation(conversationId: string): Promise<void> {
    await apiClient.patch(`/api/v1/conversations/${conversationId}/unarchive`);
  },

  /**
   * Fetch archived conversations.
   * Endpoint: GET /api/v1/conversations/show/archives
   *
   * The response embeds messages inside each conversation object so we
   * extract them into a messagesMap keyed by conversation ID. This lets
   * the archived-chats page load message detail without a second API call.
   */
  async fetchArchivedConversations(params?: { page?: number; limit?: number; conversationId?: string }): Promise<{
    conversations: Conversation[];
    messagesMap: Record<string, ConversationMessage[]>;
    pagination: ConversationsListResponse['pagination'];
  }> {
    const query: Record<string, string | number> = {};
    if (params?.page != null) query.page = params.page;
    if (params?.limit != null) query.limit = params.limit;
    if (params?.conversationId) query.conversationId = params.conversationId;

    const { data } = await apiClient.get<{
      conversations: (ConversationApiResponse & { messages?: ConversationMessage[] })[];
      pagination: ConversationsListResponse['pagination'];
    }>('/api/v1/conversations/show/archives', { params: query });

    const raw = data.conversations ?? [];
    const messagesMap: Record<string, ConversationMessage[]> = {};
    raw.forEach((c) => {
      if (c.messages) messagesMap[c._id] = c.messages;
    });

    return {
      conversations: raw.map(transformConversation),
      messagesMap,
      pagination: data.pagination,
    };
  },

  /**
   * Search across all archived conversations (both assistant and agent).
   * Endpoint: GET /api/v1/conversations/show/archives/search
   *
   * Returns a unified, paginated list sorted by lastActivityAt desc.
   * Each result includes a `source` field ('assistant' | 'agent') and
   * optionally `agentKey` for agent conversations.
   */
  async searchArchivedConversations(params: {
    search: string;
    page?: number;
    limit?: number;
  }): Promise<{
    conversations: (Conversation & { source: 'assistant' | 'agent'; agentKey?: string })[];
    pagination: ConversationsListResponse['pagination'];
    summary: {
      totalMatches: number;
      assistantMatches: number;
      agentMatches: number;
      searchQuery: string;
    };
  }> {
    const query: Record<string, string | number> = { search: params.search };
    if (params.page != null) query.page = params.page;
    if (params.limit != null) query.limit = params.limit;

    const { data } = await apiClient.get<{
      conversations: (ConversationApiResponse & {
        source: 'assistant' | 'agent';
        agentKey?: string;
      })[];
      pagination: ConversationsListResponse['pagination'];
      summary: {
        totalMatches: number;
        assistantMatches: number;
        agentMatches: number;
        searchQuery: string;
      };
    }>('/api/v1/conversations/show/archives/search', { params: query });

    return {
      conversations: (data.conversations ?? []).map((c) => ({
        ...transformConversation(c),
        source: c.source,
        agentKey: c.agentKey,
      })),
      pagination: data.pagination,
      summary: data.summary,
    };
  },

  /**
   * Fetch hub root nodes for the chat collection picker.
   *
   * Endpoint: GET /api/v1/knowledgeBase/knowledge-hub/nodes
   *
   * Returns hub root items (KB connector, other connectors, COLLECTION roots) for one page.
   * The chat UI paginates with `page` / `limit` and merges `serverPagination` when present.
   * Chevron rules live in `collections-tab` (`showExpandChevron`).
   */
  async listCollectionsForChat(params?: { page?: number; limit?: number }): Promise<ListCollectionsForChatResult> {
    const page = params?.page ?? 1;
    const limit = params?.limit ?? 100;
    const { data } = await apiClient.get<KnowledgeHubNodesResponse>(
      '/api/v1/knowledgeBase/knowledge-hub/nodes',
      {
        params: {
          page,
          limit,
          sortBy: 'updatedAt',
          sortOrder: 'desc',
        },
      }
    );

    const knowledgeBases: KnowledgeBaseForChat[] = data.items.map((item) => ({
      id: item.id,
      name: item.name,
      nodeType: item.nodeType,
      hasChildren: item.hasChildren,
      origin: item.origin,
      connector: item.connector,
      subType: item.subType,
      createdAtTimestamp: item.createdAt ?? 0,
      updatedAtTimestamp: item.updatedAt ?? 0,
      createdBy: '',
      userRole: '',
      folders: [],
    }));
    return {
      knowledgeBases,
      requestedPage: page,
      requestedLimit: limit,
      serverPagination: data.pagination ?? null,
    };
  },

  /**
   * Fetch available LLM models for the org.
   * Endpoint: GET /api/v1/configurationManager/ai-models/available/llm
   */
  async fetchAvailableLlms(): Promise<AvailableLlmModel[]> {
    const { data } = await apiClient.get<{ status: string; models: AvailableLlmModel[]; message: string }>(
      '/api/v1/configurationManager/ai-models/available/llm'
    );
    return data.models ?? [];
  },

  // ── Deprecated: knowledge-hub/nodes approach (commented out) ──
  // The knowledge-hub/nodes endpoint supports richer data (per-item counts,
  // nodeTypes filtering) but was returning incorrect results. Kept for
  // reference in case we switch back when the endpoint is fixed.
  //
  // async listCollectionsForChat_knowledgeHub(params?: { q?: string; page?: number; limit?: number }) {
  //   const { data } = await apiClient.get<CollectionsForChatResponse_KnowledgeHub>(
  //     '/api/v1/knowledgeBase/knowledge-hub/nodes',
  //     {
  //       params: {
  //         nodeTypes: 'kb',
  //         include: 'counts',
  //         sortBy: 'updatedAt',
  //         sortOrder: 'desc',
  //         page: 1,
  //         limit: 100,
  //         ...params,
  //       },
  //     }
  //   );
  //   return data;
  // },
};

// ── Types for listCollectionsForChat ──

/**
 * Minimal KB node shape from GET /api/v1/knowledgeBase/knowledge-hub/nodes
 */
interface KbNodeFromHubApi {
  id: string;
  name: string;
  nodeType: string;
  hasChildren?: boolean;
  origin?: string;
  connector?: string;
  subType?: string;
  updatedAt?: number;
  createdAt?: number;
}

interface KnowledgeHubNodesResponse {
  success: boolean;
  items: KbNodeFromHubApi[];
  error?: string | null;
  pagination?: {
    page?: number;
    limit?: number;
    totalItems?: number;
    totalPages?: number;
    hasNext?: boolean;
    hasPrev?: boolean;
  } | null;
}

/** One page from {@link ChatApi.listCollectionsForChat}. */
export interface ListCollectionsForChatResult {
  knowledgeBases: KnowledgeBaseForChat[];
  requestedPage: number;
  requestedLimit: number;
  serverPagination: KnowledgeHubNodesResponse['pagination'];
}

/**
 * Normalized knowledge base shape used internally in the chat collection picker.
 */
export interface KnowledgeBaseForChat {
  id: string;
  name: string;
  nodeType: string;
  hasChildren?: boolean;
  origin?: string;
  connector?: string;
  subType?: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  createdBy: string;
  userRole: string;
  folders: string[];
}

// ── AI Models Config ──

/** Shape of a single AI model entry from the config endpoint */
export interface AiModelConfig {
  modelKey: string;
  modelName: string;
  provider: string;
  isEnabled: boolean;
  isDefault?: boolean;
}

/** Response envelope for GET /api/v1/configurationManager/aiModelsConfig */
export interface AiModelsConfigResponse {
  models: AiModelConfig[];
}

/**
 * Fetch the org's AI model configuration.
 *
 * Returns the list of models available for chat (with their keys and names).
 * Used by the runtime to dynamically pick the model instead of hardcoding.
 */
export async function fetchAiModelsConfig(): Promise<AiModelConfig[]> {
  const { data } = await apiClient.get<AiModelsConfigResponse>(
    '/api/v1/configurationManager/aiModelsConfig',
  );
  return data.models ?? [];
}

// ============================================
// Organisation
// ============================================

export interface OrgResponse {
  _id: string;
  registeredName: string;
  shortName: string;
  domain: string;
  contactEmail: string;
  accountType: string;
  onBoardingStatus: string;
}

/**
 * Fetch the current organisation details.
 */
export async function fetchOrg(): Promise<OrgResponse> {
  const { data } = await apiClient.get<OrgResponse>('/api/v1/org');
  return data;
}

/**
 * Fetch the organisation logo and return a local blob URL.
 * Returns null if no logo is set or the request fails.
 */
export async function fetchOrgLogo(): Promise<string | null> {
  try {
    const response = await apiClient.get<Blob>('/api/v1/org/logo', {
      responseType: 'blob',
    });
    return URL.createObjectURL(response.data);
  } catch {
    return null;
  }
}

/**
 * Module-level singleton — guarantees a single fetch per page load
 * regardless of React StrictMode double-mounting or component remounts.
 */
let _orgWithLogoPromise: Promise<{ org: OrgResponse | null; logoUrl: string | null }> | null = null;

export function fetchOrgWithLogo(): Promise<{ org: OrgResponse | null; logoUrl: string | null }> {
  if (!_orgWithLogoPromise) {
    _orgWithLogoPromise = Promise.allSettled([fetchOrg(), fetchOrgLogo()]).then(
      ([orgRes, logoRes]) => ({
        org: orgRes.status === 'fulfilled' ? orgRes.value : null,
        logoUrl: logoRes.status === 'fulfilled' ? logoRes.value : null,
      })
    );
  }
  return _orgWithLogoPromise;
}
