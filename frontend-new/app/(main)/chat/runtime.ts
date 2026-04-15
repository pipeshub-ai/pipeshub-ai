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
import { AgentsApi } from '@/app/(main)/agents/api';
import { useChatStore } from './store';
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

      let agentStreamTools: string[] = [];
      if (effectiveAgentId) {
        if (currentSlot.agentStreamTools && currentSlot.agentStreamTools.length > 0) {
          agentStreamTools = currentSlot.agentStreamTools;
        } else if (agentIdFromUrl === effectiveAgentId) {
          agentStreamTools = currentState.agentStreamTools ?? [];
        } else {
          try {
            const { toolFullNames } = await AgentsApi.getAgent(effectiveAgentId);
            agentStreamTools = toolFullNames ?? [];
          } catch {
            agentStreamTools = [];
          }
        }
      }

      const request: StreamChatRequest = {
        query,
        ...(currentState.settings.selectedModel ?? currentState.settings.defaultModel ?? { modelKey: '', modelName: '', modelFriendlyName: '' }),
        ...buildStreamRequestModeFields(currentState.settings),
        filters: {
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
              agentStreamTools,
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

