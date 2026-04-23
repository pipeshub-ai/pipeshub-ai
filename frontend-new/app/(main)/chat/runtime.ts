/**
 * External Store Runtime bridge for assistant-ui.
 *
 * Provides:
 * 1. `buildExternalStoreConfig(activeSlotId)` — returns the config object
 *    consumed by `useExternalStoreRuntime`. Reads `messages` and `isRunning`
 *    from the active slot; wires `onNew` and `onCancel` to streaming.ts.
 *
 * 2. `loadHistoricalMessages()` — transforms backend ConversationMessage[]
 *    into ThreadMessageLike[] (used when initializing a slot).
 */

import type { ExternalStoreAdapter } from '@assistant-ui/react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { useChatStore, ctxKeyFromAgent, getEffectiveModel } from './store';
import { streamMessageForSlot, cancelStreamForSlot } from './streaming';
import { buildStreamRequestModeFields, type ConversationMessage, type StreamChatRequest } from './types';
import {
  buildCitationMapsFromApi,
} from './components/message-area/response-tabs/citations';

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
): Array<{ id: string; name: string }> | undefined {
  const raw = message.metadata?.custom?.collections;
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: Array<{ id: string; name: string }> = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const id = (item as { id?: unknown }).id;
    if (typeof id !== 'string') continue;
    const name = (item as { name?: unknown }).name;
    out.push({ id, name: typeof name === 'string' ? name : '' });
  }
  return out.length > 0 ? out : undefined;
}

/**
 * Transform backend conversation messages into assistant-ui thread format.
 *
 * Builds CitationMaps from CitationApiResponse for each bot_response.
 */
export function loadHistoricalMessages(
  messages: ConversationMessage[]
): ThreadMessageLike[] {
  return messages.map((msg) => ({
    // Stable per-message id — keeps list keys and citation popovers from remounting
    // on every `messages` ref replace (e.g. SSE complete or history refresh).
    id: msg._id,
    role: msg.messageType === 'user_query' ? ('user' as const) : ('assistant' as const),
    content: [
      {
        type: 'text' as const,
        text: msg.content,
      },
    ],
    metadata:
      msg.messageType === 'bot_response'
        ? {
            custom: {
              messageId: msg._id,
              citationMaps: buildCitationMapsFromApi(msg.citations || []),
              confidence: msg.confidence,
              modelInfo: msg.modelInfo,
              feedbackInfo: msg.feedback?.[0] || undefined,
            },
          }
        : undefined,
  }));
}

/**
 * Build the ExternalStoreAdapter config for `useExternalStoreRuntime`.
 *
 * This function is called on every render of the chat page.
 * It reads the active slot's messages + streaming state and provides
 * `onNew` / `onCancel` callbacks routed to slot-scoped streaming.
 *
 * @param activeSlotId — current active slot key (or null for new chat screen)
 */
export function buildExternalStoreConfig(
  activeSlotId: string | null
): ExternalStoreAdapter<ThreadMessageLike> {
  const state = useChatStore.getState();
  const slot = activeSlotId ? state.slots[activeSlotId] : null;

  return {
    messages: slot?.messages ?? [],
    isRunning: slot?.isStreaming ?? false,

    // Required when T = ThreadMessageLike (identity — our messages are already ThreadMessageLike)
    convertMessage: (msg: ThreadMessageLike) => msg,

    onNew: async (message) => {
      // Read activeSlotId from store at invocation time — NOT from the
      // closure. ChatInputWrapper.handleSend creates a slot and sets
      // activeSlotId synchronously in Zustand before calling
      // threadRuntime.append(), but React hasn't re-rendered yet, so
      // the closure's `activeSlotId` is still stale (null for new chats).
      const targetSlotId = useChatStore.getState().activeSlotId;
      if (!targetSlotId) return;

      const query = extractTextContent(message.content);
      if (!query.trim()) return;

      const currentState = useChatStore.getState();
      const currentSlot = currentState.slots[targetSlotId];
      if (!currentSlot) return;

      // Extract KB filter from message metadata (collections attached at send-time)
      const msgCollections = readKbCollectionsFromMessage(message);
      const kbFilter =
        msgCollections && msgCollections.length > 0
          ? msgCollections.map((c) => c.id)
          : currentState.settings.filters.kb;

      const urlParams =
        typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
      const rawUrlAgent = urlParams?.get('agentId');
      const agentIdFromUrl = rawUrlAgent?.trim() ? rawUrlAgent : undefined;
      const slotAgent = currentSlot.threadAgentId?.trim() || null;
      const effectiveAgentId = slotAgent ?? agentIdFromUrl ?? undefined;

      const toolsSel = currentState.agentStreamTools;
      const toolCatalog = currentState.agentToolCatalogFullNames;
      /** Agent streams only: store `null` = all tools → full catalog `fullName`s; else explicit list. */
      const streamTools = !effectiveAgentId
        ? []
        : toolsSel === null
          ? [...toolCatalog]
          : [...toolsSel];

      // Resolve the model for the CURRENT context (agent or assistant) so the
      // submitted payload matches exactly what the chat input pill shows.
      const modelCtxKey = ctxKeyFromAgent(effectiveAgentId ?? null);
      const effectiveModel = getEffectiveModel(modelCtxKey) ?? {
        modelKey: '',
        modelName: '',
        modelFriendlyName: '',
      };

      const isAgent = Boolean(effectiveAgentId);
      const knowledgeScope = currentState.agentKnowledgeScope;
      const knowledgeDefaults = currentState.agentKnowledgeDefaults;
      /** UI “full knowledge” keeps `scope` null — still send explicit ids like the panel shows. */
      const resolvedAgentKnowledge =
        isAgent && knowledgeScope === null ? knowledgeDefaults : knowledgeScope;

      const request: StreamChatRequest = {
        query,
        ...effectiveModel,
        ...buildStreamRequestModeFields(currentState.settings),
        filters: isAgent
          ? {
              apps: (resolvedAgentKnowledge?.apps ?? []).filter(
                (id): id is string => typeof id === 'string' && id.trim().length > 0
              ),
              kb: (resolvedAgentKnowledge?.kb ?? []).filter(
                (id): id is string => typeof id === 'string' && id.trim().length > 0
              ),
            }
          : {
              // KB-origin nodes from knowledge-hub/nodes have nodeType "app" and
              // must go in the apps[] filter, not kb[]. The store still uses the
              // "kb" key internally (UI naming) but the API contract is apps[].
              apps: [...currentState.settings.filters.apps, ...kbFilter],
              kb: [],
            },
        conversationId: currentSlot.convId || undefined,
        ...(effectiveAgentId
          ? {
              agentId: effectiveAgentId,
              agentStreamTools: streamTools,
            }
          : {}),
      };

      // Fire-and-forget — streaming.ts handles all state updates
      streamMessageForSlot(targetSlotId, query, request);
    },

    onCancel: async () => {
      const targetSlotId = useChatStore.getState().activeSlotId;
      if (targetSlotId) {
        cancelStreamForSlot(targetSlotId);
      }
    },
  };
}

