/**
 * External Store Runtime bridge for assistant-ui.
 *
 * Provides:
 * 1. `buildExternalStoreConfig(activeSlotId)` â€” returns the config object
 *    consumed by `useExternalStoreRuntime`. Reads `messages` and `isRunning`
 *    from the active slot; wires `onNew` and `onCancel` to streaming.ts.
 *
 * 2. `loadHistoricalMessages()` â€” transforms backend ConversationMessage[]
 *    into ThreadMessageLike[] (used when initializing a slot).
 */

import type { ExternalStoreAdapter } from '@assistant-ui/react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { useChatStore, ctxKeyFromAgent, getEffectiveModel } from './store';
import { streamMessageForSlot, cancelStreamForSlot } from './streaming';
import { fetchModelsForContext } from './utils/fetch-models-for-context';
import {
  buildAssistantApiFilters,
  buildStreamRequestModeFields,
  type AppliedFilterNode,
  type AppliedFilters,
  type AttachmentRef,
  type AskUserQuestionPayload,
  type ChatCollectionAttachment,
  type ChatKnowledgeFilters,
  type ChatSettings,
  type ConversationMessage,
  type PendingAskUserQuestion,
  type StreamChatRequest,
} from './types';
import {
  buildCitationMapsFromApi,
} from './components/message-area/response-tabs/citations';
import { getClientTimezone, getClientCurrentTime } from './utils/client-time';

/** Non-empty query required by the chat API when the user sends attachments only (matches Slack bot). */
const ATTACHMENT_ONLY_STREAM_QUERY = 'See below attached file(s).';

/**
 * Extract text content from assistant-ui message content
 */
function extractTextContent(content: ThreadMessageLike['content']): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter(
      (part): part is { type: 'text'; text?: string } =>
        typeof part === 'object' && part !== null && 'type' in part && part.type === 'text'
    )
    .map((part) => (typeof part.text === 'string' ? part.text : ''))
    .join('');
}

/** Plain text of a thread message (used by streaming + message list). */
export function getThreadMessagePlainText(message: ThreadMessageLike): string {
  return extractTextContent(message.content);
}

/** KB collections attached on send (see chat input metadata). */
function readKbCollectionsFromMessage(
  message: ThreadMessageLike
): ChatCollectionAttachment[] | undefined {
  const raw = message.metadata?.custom?.collections;
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: ChatCollectionAttachment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const id = (item as { id?: unknown }).id;
    if (typeof id !== 'string') continue;
    const name = (item as { name?: unknown }).name;
    const kindRaw = (item as { kind?: unknown }).kind;
    const kind =
      kindRaw === 'recordGroup' || kindRaw === 'collectionRoot'
        ? kindRaw
        : undefined;
    out.push({
      id,
      name: typeof name === 'string' ? name : '',
      ...(kind ? { kind } : {}),
    });
  }
  return out.length > 0 ? out : undefined;
}

/** Mirrors chat-send scope resolution from the last user message that carried KB attachments. */
function resolveAssistantFiltersFromSlot(
  slot: { messages: ThreadMessageLike[] },
  settings: ChatSettings
): ChatKnowledgeFilters {
  const storeApps = settings.filters.apps ?? [];
  const storeKb = settings.filters.kb ?? [];
  for (let i = slot.messages.length - 1; i >= 0; i -= 1) {
    const m = slot.messages[i];
    if (m.role !== 'user') continue;
    const msgCollections = readKbCollectionsFromMessage(m);
    if (msgCollections && msgCollections.length > 0) {
      const msgRootIds = msgCollections
        .filter((c) => (c.kind ?? 'collectionRoot') !== 'recordGroup')
        .map((c) => c.id);
      const msgKbIds = msgCollections.filter((c) => c.kind === 'recordGroup').map((c) => c.id);
      return {
        apps: [...new Set([...storeApps, ...msgRootIds])],
        kb: [...msgKbIds],
      };
    }
  }
  return { apps: [...storeApps], kb: [...storeKb] };
}

/**
 * Resolve KB/app filters for a chat POST â€” either from the outgoing composer message
 * (normal send) or from the latest prior user turn that carried attachments (questionnaire submit).
 */
export function resolveAssistantFiltersForChatSubmit(
  slot: { messages: ThreadMessageLike[] },
  settings: ChatSettings,
  outgoingMessage?: ThreadMessageLike
): ChatKnowledgeFilters {
  const storeApps = settings.filters.apps ?? [];
  const storeKb = settings.filters.kb ?? [];

  if (outgoingMessage) {
    const msgCollections = readKbCollectionsFromMessage(outgoingMessage);
    if (msgCollections && msgCollections.length > 0) {
      const msgRootIds = msgCollections
        .filter((c) => (c.kind ?? 'collectionRoot') !== 'recordGroup')
        .map((c) => c.id);
      const msgKbIds = msgCollections.filter((c) => c.kind === 'recordGroup').map((c) => c.id);
      return {
        apps: [...new Set([...storeApps, ...msgRootIds])],
        kb: [...msgKbIds],
      };
    }
    return { apps: [...storeApps], kb: [...storeKb] };
  }

  return resolveAssistantFiltersFromSlot(slot, settings);
}

/**
 * Build the streaming POST body for the given slot (agent vs assistant, filters,
 * tools, model). Used by questionnaire submit and the chat composer bridge.
 */
export function buildStreamChatRequestForSlot(
  slotId: string,
  query: string,
  outgoingMessage?: ThreadMessageLike
): StreamChatRequest | null {
  const currentState = useChatStore.getState();
  const currentSlot = currentState.slots[slotId];
  if (!currentSlot) return null;

  const assistantFilters = resolveAssistantFiltersForChatSubmit(
    currentSlot,
    currentState.settings,
    outgoingMessage
  );

  const urlParams =
    typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
  const rawUrlAgent = urlParams?.get('agentId');
  const agentIdFromUrl = rawUrlAgent?.trim() ? rawUrlAgent : undefined;
  const slotAgent = currentSlot.threadAgentId?.trim() || null;
  const effectiveAgentId = slotAgent ?? agentIdFromUrl ?? undefined;

  const isUniversalAgentMode =
    !effectiveAgentId && currentState.settings.queryMode === 'agent';

  const toolsSel = effectiveAgentId
    ? currentState.agentStreamTools
    : isUniversalAgentMode
      ? currentState.universalAgentStreamTools
      : null;

  const toolCatalog = effectiveAgentId
    ? currentState.agentToolCatalogFullNames
    : currentState.universalAgentToolCatalogFullNames;

  const stripInstancePrefix = (key: string) => {
    const colon = key.indexOf(':');
    return colon >= 0 ? key.slice(colon + 1) : key;
  };

  const streamTools =
    effectiveAgentId || isUniversalAgentMode
      ? [...new Set((toolsSel === null ? [...toolCatalog] : [...toolsSel]).map(stripInstancePrefix))]
      : [];

  const modelCtxKey = ctxKeyFromAgent(effectiveAgentId ?? null);
  const effectiveModel = getEffectiveModel(modelCtxKey) ?? {
    modelKey: '',
    modelName: '',
    modelFriendlyName: '',
  };

  const isAgent = Boolean(effectiveAgentId);
  const knowledgeScope = currentState.agentKnowledgeScope;
  const knowledgeDefaults = currentState.agentKnowledgeDefaults;
  const resolvedAgentKnowledge =
    isAgent && knowledgeScope === null ? knowledgeDefaults : knowledgeScope;

  const resolvedFilters = isAgent
    ? {
        apps: (resolvedAgentKnowledge?.apps ?? []).filter(
          (id): id is string => typeof id === 'string' && id.trim().length > 0
        ),
        kb: (resolvedAgentKnowledge?.kb ?? []).filter(
          (id): id is string => typeof id === 'string' && id.trim().length > 0
        ),
      }
    : buildAssistantApiFilters(assistantFilters);

  const metaCache = currentState.collectionMetaCache;
  const buildAppliedFilterNodes = (ids: string[]): AppliedFilterNode[] =>
    ids
      .filter((id) => id.trim().length > 0)
      .map((id) => {
        const meta = metaCache[id];
        return {
          id,
          name: meta?.name ?? currentState.collectionNamesCache[id] ?? id,
          nodeType: meta?.nodeType ?? '',
          connector: meta?.connector ?? '',
        };
      });

  const hasFilters = resolvedFilters.apps.length > 0 || resolvedFilters.kb.length > 0;
  const appliedFilters: AppliedFilters | undefined = hasFilters
    ? {
        apps: buildAppliedFilterNodes(resolvedFilters.apps),
        kb: buildAppliedFilterNodes(resolvedFilters.kb),
      }
    : isAgent
      ? { apps: [], kb: [] }
      : undefined;

  const request: StreamChatRequest = {
    query,
    ...effectiveModel,
    ...buildStreamRequestModeFields(currentState.settings),
    timezone: getClientTimezone(),
    currentTime: getClientCurrentTime(),
    filters: resolvedFilters,
    ...(appliedFilters ? { appliedFilters } : {}),
    conversationId: currentSlot.convId || undefined,
    ...(effectiveAgentId
      ? {
          agentId: effectiveAgentId,
          agentStreamTools: streamTools,
        }
      : isUniversalAgentMode && toolsSel !== null
        ? {
            agentStreamTools: streamTools,
          }
        : {}),
  };

  return request;
}

export interface LoadHistoricalResult {
  messages: ThreadMessageLike[];
  unansweredAskUserQuestion: PendingAskUserQuestion | null;
}

/**
 * Transform backend conversation messages into assistant-ui thread format.
 *
 * Builds CitationMaps from CitationApiResponse for each bot_response.
 *
 * Handles `tool_call` messages:
 *   - Filters them out of the output (no ThreadMessageLike entry).
 *   - When a `tool_call` with `ask_user_question` precedes a `bot_response`:
 *     â€¢ If a subsequent `user_query` exists â†’ attaches payload as
 *       `persistedAskUserQuestion` in metadata (read-only display).
 *     â€¢ If no subsequent `user_query` â†’ returns it as
 *       `unansweredAskUserQuestion` for the caller to restore interactive state.
 */
export function loadHistoricalMessages(
  messages: ConversationMessage[]
): LoadHistoricalResult {
  const result: ThreadMessageLike[] = [];
  let toolPayload: AskUserQuestionPayload | null = null;
  let lastUnansweredAssistantId: string | null = null;
  let lastUnansweredPayload: AskUserQuestionPayload | null = null;

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];

    if (msg.messageType === 'tool_call') {
      const askTool = msg.tools?.find(t => t.toolName === 'ask_user_question');
      if (askTool?.toolResult) {
        const tr = askTool.toolResult as Record<string, unknown>;
        if (tr.name === 'ask_user_question' && Array.isArray(tr.questions)) {
          toolPayload = tr as unknown as AskUserQuestionPayload;
        }
      }
      continue;
    }

    if (msg.messageType === 'error') {
      toolPayload = null;
      result.push({
        id: msg._id,
        role: 'assistant' as const,
        content: [{ type: 'text' as const, text: msg.content || 'An error occurred. Please try again.' }],
        metadata: {
          custom: {
            citationMaps: buildCitationMapsFromApi([]),
          },
        },
      });
      continue;
    }

    if (msg.messageType === 'bot_response') {
      const capturedPayload = toolPayload;
      toolPayload = null;

      let isAnswered = false;
      if (capturedPayload) {
        for (let j = i + 1; j < messages.length; j++) {
          if (messages[j].messageType === 'tool_call') continue;
          if (messages[j].messageType === 'user_query') { isAnswered = true; }
          break;
        }
        if (!isAnswered) {
          lastUnansweredAssistantId = msg._id;
          lastUnansweredPayload = capturedPayload;
        }
      }

      const feedbackEntry = (msg.feedback as Array<{ isHelpful?: boolean }> | undefined)?.[0];
      const feedbackInfo = feedbackEntry?.isHelpful === true
        ? { value: 'like' as const }
        : feedbackEntry?.isHelpful === false
          ? { value: 'dislike' as const }
          : undefined;

      result.push({
        id: msg._id,
        role: 'assistant' as const,
        content: [{ type: 'text' as const, text: msg.content }],
        metadata: {
          custom: {
            messageId: msg._id,
            citationMaps: buildCitationMapsFromApi(msg.citations || []),
            confidence: msg.confidence,
            modelInfo: msg.modelInfo,
            ...(feedbackInfo ? { feedbackInfo } : {}),
            ...(capturedPayload && isAnswered
              ? { persistedAskUserQuestion: capturedPayload }
              : {}),
          },
        },
      });
      continue;
    }

    // user_query
    toolPayload = null;
    result.push({
      id: msg._id,
      role: 'user' as const,
      content: [{ type: 'text' as const, text: msg.content }],
      metadata: {
        custom: {
          createdAt: msg.createdAt,
          ...(msg.appliedFilters ? { appliedFilters: msg.appliedFilters } : {}),
          ...(msg.attachments?.length ? { attachments: msg.attachments } : {}),
        },
      },
    });
  }

  let unanswered: PendingAskUserQuestion | null = null;
  if (lastUnansweredAssistantId && lastUnansweredPayload) {
    unanswered = {
      assistantMessageId: lastUnansweredAssistantId,
      payload: lastUnansweredPayload,
      answers: {},
      status: 'pending',
    };
  }

  return { messages: result, unansweredAskUserQuestion: unanswered };
}

/** Attachment refs attached on send (see chat input metadata). */
function readAttachmentsFromMessage(
  message: ThreadMessageLike
): AttachmentRef[] | undefined {
  const raw = message.metadata?.custom?.attachments;
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: AttachmentRef[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const recordId = (item as { recordId?: unknown }).recordId;
    const virtualRecordId = (item as { virtualRecordId?: unknown }).virtualRecordId;
    if (typeof recordId !== 'string' || typeof virtualRecordId !== 'string') continue;
    out.push({
      recordId,
      recordName: String((item as { recordName?: unknown }).recordName ?? ''),
      mimeType: String((item as { mimeType?: unknown }).mimeType ?? ''),
      extension: String((item as { extension?: unknown }).extension ?? ''),
      virtualRecordId,
    });
  }
  return out.length > 0 ? out : undefined;
}

/**
 * Build the ExternalStoreAdapter config for `useExternalStoreRuntime`.
 *
 * This function is called on every render of the chat page.
 * It reads the active slot's messages + streaming state and provides
 * `onNew` / `onCancel` callbacks routed to slot-scoped streaming.
 *
 * @param activeSlotId â€” current active slot key (or null for new chat screen)
 */
export function buildExternalStoreConfig(
  activeSlotId: string | null
): ExternalStoreAdapter<ThreadMessageLike> {
  const state = useChatStore.getState();
  const slot = activeSlotId ? state.slots[activeSlotId] : null;

  return {
    messages: slot?.messages ?? [],
    isRunning: slot?.isStreaming ?? false,

    // Required when T = ThreadMessageLike (identity â€” our messages are already ThreadMessageLike)
    convertMessage: (msg: ThreadMessageLike) => msg,

    onNew: async (message) => {
      // Read activeSlotId from store at invocation time â€” NOT from the
      // closure. ChatInputWrapper.handleSend creates a slot and sets
      // activeSlotId synchronously in Zustand before calling
      // threadRuntime.append(), but React hasn't re-rendered yet, so
      // the closure's `activeSlotId` is still stale (null for new chats).
      const targetSlotId = useChatStore.getState().activeSlotId;
      if (!targetSlotId) return;

      const displayQuery = extractTextContent(message.content).trim();
      const msgAttachmentsEarly = readAttachmentsFromMessage(message);
      if (!displayQuery && (!msgAttachmentsEarly || msgAttachmentsEarly.length === 0)) return;

      const currentState = useChatStore.getState();
      const currentSlot = currentState.slots[targetSlotId];
      if (!currentSlot) return;
      // Safety net: ChatInputWrapper blocks user sends while streaming; only programmatic api call `threadRuntime.append` reaches here.
      if (currentSlot.isStreaming) return;

      const msgAttachments = msgAttachmentsEarly;

      const apiQuery =
        displayQuery ||
        (msgAttachments && msgAttachments.length > 0
          ? ATTACHMENT_ONLY_STREAM_QUERY
          : '');
      if (!apiQuery) return;

      const request = buildStreamChatRequestForSlot(targetSlotId, apiQuery, message);
      if (!request) return;

      if (msgAttachments) {
        request.attachments = msgAttachments;
      }

      // Fire-and-forget â€” streaming.ts handles all state updates.
      // Keep `displayQuery` for the slot user row + streamingQuestion so attachment-only turns still match an empty text bubble.
      streamMessageForSlot(targetSlotId, displayQuery, request);
    },

    onCancel: async () => {
      const targetSlotId = useChatStore.getState().activeSlotId;
      if (targetSlotId) {
        cancelStreamForSlot(targetSlotId);
      }
    },
  };
}
