import { apiClient, streamSSERequest, SSEEvent } from '@/lib/api';
import {
  ChatMessage,
  Conversation,
  ConversationMessage,
  ConversationsListResponse,
  ConversationApiResponse,
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
  AvailableLlmModel,
  SearchRequest,
  SearchResponse,
  streamChatModeToAgentApiChatMode,
} from './types';

export interface StreamMessageCallbacks {
  onConnected?: (data: SSEConnectedEvent) => void;
  onStatus?: (data: SSEStatusEvent) => void;
  onChunk?: (data: SSEAnswerChunkEvent) => void;
  onComplete?: (data: SSECompleteEvent) => void;
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
  /** Passed to GET /api/v1/conversations?search= — server matches title and message content */
  search?: string;
  signal?: AbortSignal;
}

// Chat API endpoints
export const ChatApi = {
  // Fetch all conversations (user's own and shared with them)
  async fetchConversations(
    page: number = 1,
    limit: number = 20,
    options?: FetchConversationsOptions
  ): Promise<{
    conversations: Conversation[];
    sharedConversations: Conversation[];
    pagination: ConversationsListResponse['pagination'];
  }> {
    const search = options?.search?.trim();
    const params: Record<string, string | number> = { page, limit };
    if (search) {
      params.search = search;
    }

    const { data } = await apiClient.get<ConversationsListResponse>(
      `/api/v1/conversations`,
      { params, signal: options?.signal }
    );

    return {
      conversations: data.conversations.map(transformConversation),
      sharedConversations: data.sharedWithMeConversations.map(transformConversation),
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
   * Stream a chat message with SSE
   * Handles new conversation creation and existing conversation messages
   */
  async streamMessage(
    request: StreamChatRequest,
    callbacks: StreamMessageCallbacks
  ): Promise<void> {
    let endpoint: string;
    let payload: Record<string, unknown>;

    if (request.agentId) {
      const agentChatMode = streamChatModeToAgentApiChatMode(request.chatMode);
      const timezone =
        typeof Intl !== 'undefined'
          ? Intl.DateTimeFormat().resolvedOptions().timeZone
          : 'UTC';
      endpoint = request.conversationId
        ? `/api/v1/agents/${request.agentId}/conversations/${request.conversationId}/messages/stream`
        : `/api/v1/agents/${request.agentId}/conversations/stream`;
      payload = {
        query: request.query,
        modelKey: request.modelKey,
        modelName: request.modelName,
        modelFriendlyName: request.modelFriendlyName ?? request.modelName,
        chatMode: agentChatMode,
        timezone,
        currentTime: new Date().toISOString(),
        tools: request.agentStreamTools ?? [],
      };
      // Omit filters when empty so the Python agent stream treats scope as
      // "derive from agent knowledge" — `{ apps: [], kb: [] }` means explicit no scope.
      const f = request.filters;
      const apps = (f?.apps ?? []).filter(
        (id): id is string => typeof id === 'string' && id.trim().length > 0
      );
      const kb = (f?.kb ?? []).filter(
        (id): id is string => typeof id === 'string' && id.trim().length > 0
      );
      if (apps.length > 0 || kb.length > 0) {
        payload.filters = { apps, kb };
      }
    } else {
      endpoint = request.conversationId
        ? `/api/v1/conversations/${request.conversationId}/messages/stream`
        : `/api/v1/conversations/stream`;
      payload = request as unknown as Record<string, unknown>;
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
    }
  ): Promise<void> {
    const endpoint = `/api/v1/conversations/${conversationId}/message/${messageId}/regenerate`;

    let receivedComplete = false;
    let lastSSEError: SSEErrorEvent | null = null;

    await streamSSERequest(
      endpoint,
      {
        modelKey: request.modelKey,
        modelName: request.modelName,
        modelFriendlyName: request.modelFriendlyName,
        chatMode: request.chatMode,
        filters: request.filters,
      } as unknown as Record<string, unknown>,
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
    }
  ): Promise<void> {
    const endpoint = `/api/v1/agents/${agentId}/conversations/${conversationId}/message/${messageId}/regenerate`;

    let receivedComplete = false;
    let lastSSEError: SSEErrorEvent | null = null;

    await streamSSERequest(
      endpoint,
      {
        modelKey: model.modelKey,
        modelName: model.modelName,
        modelProvider: model.modelProvider,
        chatMode: model.chatMode,
      },
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
      { signal }
    );
    return data;
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
  async fetchArchivedConversations(): Promise<{
    conversations: Conversation[];
    messagesMap: Record<string, ConversationMessage[]>;
    pagination: ConversationsListResponse['pagination'];
  }> {
    const { data } = await apiClient.get<{
      conversations: (ConversationApiResponse & { messages?: ConversationMessage[] })[];
      pagination: ConversationsListResponse['pagination'];
    }>('/api/v1/conversations/show/archives');

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
   * Fetch collections (knowledge bases) for the chat collection picker.
   *
   * Endpoint: GET /api/v1/knowledgeBase/knowledge-hub/nodes
   *
   * Returns root-level KB nodes. IDs returned here are passed in `filters.kb[]`
   * to the streaming endpoint. Search is performed client-side.
   */
  async listCollectionsForChat() {
    const { data } = await apiClient.get<KnowledgeHubNodesResponse>(
      '/api/v1/knowledgeBase/knowledge-hub/nodes',
      {
        params: {
          page: 1,
          limit: 100,
          sortBy: 'updatedAt',
          sortOrder: 'desc',
          origins: 'KB',
        },
      }
    );
    const knowledgeBases: KnowledgeBaseForChat[] = data.items.map((item) => ({
      id: item.id,
      name: item.name,
      createdAtTimestamp: item.createdAt ?? 0,
      updatedAtTimestamp: item.updatedAt ?? 0,
      createdBy: '',
      userRole: '',
      folders: [],
    }));
    return { knowledgeBases };
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
  updatedAt?: number;
  createdAt?: number;
}

interface KnowledgeHubNodesResponse {
  success: boolean;
  items: KbNodeFromHubApi[];
}

/**
 * Normalized knowledge base shape used internally in the chat collection picker.
 */
export interface KnowledgeBaseForChat {
  id: string;
  name: string;
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
