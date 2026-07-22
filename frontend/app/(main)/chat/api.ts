import { apiClient, streamSSERequest, SSEEvent } from '@/lib/api';
import { CONVERSATION_MESSAGES_PAGE_SIZE } from './constants';
import {
  ChatMessage,
  Conversation,
  ConversationMessage,
  ConversationPagination,
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
  SSEAskUserQuestionEvent,
  MessagePart,
  AvailableLlmModel,
  SearchRequest,
  SearchResponse,
  streamChatModeToAgentApiChatMode,
  AttachmentRef,
} from './types';
import { getClientTimezone, getClientCurrentTime } from './utils/client-time';
import { createAGUIEventHandler, type AGUIStreamTracking } from './agui-event-handler';

/**
 * Rollout switch for the AG-UI migration plan's Phase 3/4c. Default ON:
 * Node (`es_controller.ts`) and Python (`AGUIEventEmitter`/`ProtocolFormatter`)
 * both negotiate `protocol: 'agui'` end-to-end, and the e2e fixtures under
 * `tests/e2e/chat/*.spec.ts` speak AG-UI frames (see `agui-sse-builder.ts`).
 * Flip back to `false` only as a kill switch if a live-backend regression
 * surfaces — the legacy dispatcher path is kept working for that reason.
 */
const USE_AGUI_PROTOCOL = true;

export interface FeedbackPayload {
  isHelpful: boolean;
  categories?: string[];
  comments?: {
    positive?: string;
    negative?: string;
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
  onAskUserQuestion?: (data: SSEAskUserQuestionEvent) => void;
  /**
   * Live chain-of-thought delta (AG-UI protocol only — see `agui-event-handler.ts`).
   * `done: true` marks the end of one reasoning turn; no legacy equivalent exists,
   * so implementations are free to no-op this until a "thinking" panel is built.
   */
  onReasoning?: (data: { delta: string; done: boolean }) => void;
  /**
   * Live agent-activity transcript snapshot (AG-UI protocol only — see
   * `agui-event-handler.ts`). Called with the FULL parts array (not a
   * delta) every time it changes, so implementations can just assign it
   * straight into slot state. No legacy equivalent; safe to no-op.
   */
  onParts?: (parts: MessagePart[]) => void;
  onError?: (error: Error) => void;
  signal?: AbortSignal;
}

interface StreamDispatchTracking {
  receivedComplete: boolean;
  lastSSEError: SSEErrorEvent | null;
}

/**
 * Legacy `{event, data}` dispatcher — byte-identical to the switch that used
 * to be duplicated across `streamMessage`/`streamRegenerate`/`streamAgentRegenerate`.
 * Extracted once so the AG-UI branch (see `runChatStream`) doesn't need a second
 * copy of every call site.
 */
function legacyEventDispatcher(
  callbacks: StreamMessageCallbacks,
  tracking: StreamDispatchTracking,
  logLabel: string,
): (event: SSEEvent) => void {
  return (event: SSEEvent) => {
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
        tracking.receivedComplete = true;
        callbacks.onComplete?.(event.data as SSECompleteEvent);
        break;
      case 'restreaming':
        callbacks.onRestreaming?.();
        break;
      case 'ask_user_question':
        callbacks.onAskUserQuestion?.(event.data as SSEAskUserQuestionEvent);
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
        tracking.lastSSEError = event.data as SSEErrorEvent;
        console.warn(`[${logLabel}] Backend warning:`, tracking.lastSSEError.message || tracking.lastSSEError.error);
        break;
      default:
        // Future / proxy-only event names — ignore silently (no user-facing noise)
        break;
    }
  };
}

/**
 * Runs one chat SSE stream and dispatches every frame to `callbacks`,
 * negotiating AG-UI vs legacy wire events in one place (AG-UI migration
 * plan, Phase 3b) — `endpoint`/`payload` stay protocol-agnostic; this is the
 * only function that adds `protocol` to the request and picks the matching
 * `onEvent` handler.
 */
async function runChatStream(
  endpoint: string,
  payload: Record<string, unknown>,
  callbacks: StreamMessageCallbacks,
  logLabel: string,
): Promise<void> {
  const body = USE_AGUI_PROTOCOL ? { ...payload, protocol: 'agui' } : payload;

  if (USE_AGUI_PROTOCOL) {
    const tracking: AGUIStreamTracking = { receivedComplete: false };
    await streamSSERequest(endpoint, body, {
      onEvent: createAGUIEventHandler(callbacks, tracking),
      onError: (error) => callbacks.onError?.(error),
      signal: callbacks.signal,
    });
    return;
  }

  const tracking: StreamDispatchTracking = { receivedComplete: false, lastSSEError: null };
  await streamSSERequest(endpoint, body, {
    onEvent: legacyEventDispatcher(callbacks, tracking, logLabel),
    onError: (error) => callbacks.onError?.(error),
    signal: callbacks.signal,
  });

  // If the stream ended without a complete event but had an error,
  // the error was fatal — propagate it. (AG-UI's RUN_ERROR is always
  // explicit and already invoked onError synchronously above, so this
  // fallback is legacy-only.)
  if (!tracking.receivedComplete && tracking.lastSSEError) {
    const errorMessage =
      tracking.lastSSEError.message || tracking.lastSSEError.error || 'Stream ended with an error';
    callbacks.onError?.(new Error(errorMessage));
  }
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
 * Sanitise an array of filter IDs: keep only non-blank strings.
 */
function sanitizeFilterIds(ids: unknown[] | null | undefined): string[] {
  return (ids ?? []).filter(
    (id): id is string => typeof id === 'string' && id.trim().length > 0,
  );
}

/**
 * Normalise an app/KB filter list and return a `{ filters }` spread fragment
 * for **non-agent** (assistant) streams.
 *
 * Only emits the key when at least one valid ID remains — so the backend
 * treats the absence of `filters` as "search everything" (correct for the
 * assistant endpoint where no scope is intentional).
 */
function buildFiltersPayload(
  apps: unknown[] | null | undefined,
  kb: unknown[] | null | undefined,
): Record<string, unknown> {
  const validApps = sanitizeFilterIds(apps);
  const validKb = sanitizeFilterIds(kb);
  return validApps.length > 0 || validKb.length > 0
    ? { filters: { apps: validApps, kb: validKb } }
    : {};
}

/**
 * Build `{ filters }` for **agent** streams.
 *
 * Always emits the key — even when both arrays are empty — because the agent
 * endpoint interprets a missing `filters` key as "all knowledge," whereas
 * `{ apps: [], kb: [] }` means "this agent has no knowledge scope."
 */
function buildAgentFiltersPayload(
  apps: unknown[] | null | undefined,
  kb: unknown[] | null | undefined,
): { filters: { apps: string[]; kb: string[] } } {
  return { filters: { apps: sanitizeFilterIds(apps), kb: sanitizeFilterIds(kb) } };
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
   * Fetch a specific conversation with paginated messages.
   * page=1 returns the most recent batch; higher pages return older batches.
   * Used when loading conversation history from sidebar click and for
   * "load older messages" infinite-scroll in the message list.
   *
   * Response is wrapped: { conversation: {...}, filters: {...}, meta: {...} }
   */
  async fetchConversation(
    conversationId: string,
    page: number = 1,
    limit: number = CONVERSATION_MESSAGES_PAGE_SIZE,
  ): Promise<{
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
    pagination: ConversationPagination;
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
        /** Optional: older API versions may not include this field. */
        pagination?: ConversationPagination;
        access: {
          isOwner: boolean;
          accessLevel: string;
        };
      };
      filters: Record<string, unknown>;
      meta: Record<string, unknown>;
    }>(`/api/v1/conversations/${conversationId}/`, {
      params: { page, limit },
    });

    const pagination = data.conversation.pagination ?? {
      page,
      limit,
      totalCount: 0,
      totalPages: 1,
      hasNextPage: false,
      hasPrevPage: page > 1,
    };

    return {
      conversation: data.conversation,
      messages: data.conversation.messages || [],
      pagination,
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
      endpoint = request.conversationId
        ? `/api/v1/agents/${request.agentId}/conversations/${request.conversationId}/messages/stream`
        : `/api/v1/agents/${request.agentId}/conversations/stream`;
      const f = request.filters;
      payload = {
        query: request.query,
        modelKey: request.modelKey,
        modelName: request.modelName,
        modelFriendlyName: request.modelFriendlyName || request.modelName,
        chatMode: agentChatMode,
        timezone: getClientTimezone(),
        currentTime: getClientCurrentTime(),
        // `undefined` (runtime.ts omits the field entirely when every tool
        // is selected) must NOT become `[]` here — an empty array means
        // "no tools" to the backend (agent.py treats `None`/missing as
        // "use every configured toolset", `[]` as an explicit empty
        // filter), the opposite of what an unfiltered selection means.
        ...(request.agentStreamTools !== undefined ? { tools: request.agentStreamTools } : {}),
        ...buildAgentFiltersPayload(f.apps, f.kb),
        ...(request.appliedFilters ? { appliedFilters: request.appliedFilters } : {}),
        ...(request.attachments?.length ? { attachments: request.attachments } : {}),
      };
    } else {
      endpoint = request.conversationId
        ? `/api/v1/conversations/${request.conversationId}/messages/stream`
        : `/api/v1/conversations/stream`;
      // Rename `agentStreamTools` → `tools` (Node.js controller reads `req.body.tools`
      // uniformly for both agent and non-agent paths) and validate filters.
      const { agentStreamTools, filters: reqFilters, attachments: reqAttachments, ...rest } = request;
      payload = {
        ...rest,
        modelFriendlyName: request.modelFriendlyName || request.modelName,
        timezone: getClientTimezone(),
        currentTime: getClientCurrentTime(),
        ...(agentStreamTools !== undefined ? { tools: agentStreamTools } : {}),
        ...buildFiltersPayload(reqFilters?.apps, reqFilters?.kb),
        ...(reqAttachments?.length ? { attachments: reqAttachments } : {}),
      };
    }

    await runChatStream(endpoint, payload, callbacks, 'Chat SSE');
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

    const body: Record<string, unknown> = {
      modelKey: request.modelKey,
      modelName: request.modelName,
      modelFriendlyName: request.modelFriendlyName || request.modelName,
      chatMode: request.chatMode,
      ...buildFiltersPayload(request.filters?.apps, request.filters?.kb),
      timezone: getClientTimezone(),
      currentTime: getClientCurrentTime(),
    };
    if (request.agentStreamTools !== undefined) {
      body.tools = request.agentStreamTools;
    }

    await runChatStream(endpoint, body, callbacks, 'Regenerate SSE');
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
      modelFriendlyName?: string;
      chatMode: AgentStrategyApiSegment;
      /** Explicit tool subset for this agent context (all tools when omitted). */
      tools?: string[];
      filters: { apps: string[]; kb: string[] };
    }
  ): Promise<void> {
    const endpoint = `/api/v1/agents/${agentId}/conversations/${conversationId}/message/${messageId}/regenerate`;

    const agentRegenBody: Record<string, unknown> = {
      modelKey: model.modelKey,
      modelName: model.modelName,
      modelFriendlyName: model.modelFriendlyName || model.modelName,
      chatMode: model.chatMode,
      timezone: getClientTimezone(),
      currentTime: getClientCurrentTime(),
      ...buildAgentFiltersPayload(model.filters.apps, model.filters.kb),
    };
    if (model.tools !== undefined) {
      agentRegenBody.tools = model.tools;
    }

    await runChatStream(endpoint, agentRegenBody, callbacks, 'Agent regenerate SSE');
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
   * Upload chat attachments (PDF / JPEG / PNG) and return the server-assigned AttachmentRef list.
   * Uses the public JWT-authenticated endpoint — not the internal scoped-token endpoint.
   */
  async uploadAttachments(
    files: File[],
    opts: { agentId?: string | null; conversationId?: string | null; signal?: AbortSignal },
  ): Promise<AttachmentRef[]> {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f, f.name));
    if (opts.conversationId) fd.append('conversationId', opts.conversationId);
    const url = opts.agentId
      ? `/api/v1/agents/${encodeURIComponent(opts.agentId)}/conversations/attachments/upload`
      : `/api/v1/conversations/attachments/upload`;
    const { data } = await apiClient.post<{ attachments: AttachmentRef[] }>(url, fd, {
      signal: opts.signal,
      // Remove the default application/json Content-Type so axios can auto-set
      // multipart/form-data with the correct boundary for this FormData upload.
      headers: { 'Content-Type': undefined },
      // Suppress the global error toast — the caller (chat-input-wrapper) shows
      // a contextual toast with the actual server error message.
      suppressErrorToast: true,
    });
    return data.attachments ?? [];
  },

  /**
   * Delete a previously uploaded chat attachment. Called fire-and-forget when
   * the user removes a chip after the upload has completed. Errors are silently
   * swallowed by callers — a failed delete is acceptable (orphan record) but
   * must never block the UI or the query.
   */
  async deleteAttachment(
    recordId: string,
    opts: { agentId?: string | null },
  ): Promise<void> {
    const url = opts.agentId
      ? `/api/v1/agents/${encodeURIComponent(opts.agentId)}/conversations/attachments/${encodeURIComponent(recordId)}`
      : `/api/v1/conversations/attachments/${encodeURIComponent(recordId)}`;
    await apiClient.delete(url, { suppressErrorToast: true });
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
