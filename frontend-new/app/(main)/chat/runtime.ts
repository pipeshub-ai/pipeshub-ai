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
import {
  buildAssistantApiFilters,
  buildStreamRequestModeFields,
  type AppliedFilterNode,
  type AppliedFilters,
  type ChatCollectionAttachment,
  type ChatKnowledgeFilters,
  type ConversationMessage,
  type StreamChatRequest,
} from './types';
import { getClientTimezone, getClientCurrentTime } from './utils/client-time';
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
              // Feedback is stored server-side but intentionally not displayed in the UI
            },
          }
        : msg.messageType === 'user_query' && msg.appliedFilters
        ? {
            custom: {
              appliedFilters: msg.appliedFilters,
              createdAt: msg.createdAt,
            },
          }
        : {
          custom: {
            createdAt: msg.createdAt,
          },
        },
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

      // Extract KB / collection-root scope from message metadata (attached at send-time)
      const msgCollections = readKbCollectionsFromMessage(message);
      const storeApps = currentState.settings.filters.apps ?? [];
      const storeKb = currentState.settings.filters.kb ?? [];

      /** Same merge as legacy `buildAssistantApiFilters(apps + collectionRootIds)` + message-time kb override. */
      let assistantFilters: ChatKnowledgeFilters;
      if (msgCollections && msgCollections.length > 0) {
        const msgRootIds = msgCollections
          .filter((c) => (c.kind ?? 'collectionRoot') !== 'recordGroup')
          .map((c) => c.id);
        const msgKbIds = msgCollections
          .filter((c) => c.kind === 'recordGroup')
          .map((c) => c.id);
        assistantFilters = {
          apps: [...new Set([...storeApps, ...msgRootIds])],
          kb: [...msgKbIds],
        };
      } else {
        assistantFilters = { apps: [...storeApps], kb: [...storeKb] };
      }

      const urlParams =
        typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
      const rawUrlAgent = urlParams?.get('agentId');
      const agentIdFromUrl = rawUrlAgent?.trim() ? rawUrlAgent : undefined;
      const slotAgent = currentSlot.threadAgentId?.trim() || null;
      const effectiveAgentId = slotAgent ?? agentIdFromUrl ?? undefined;

      const isUniversalAgentMode =
        !effectiveAgentId && currentState.settings.queryMode === 'agent';

      // Resolve tool selection for the active context:
      // - URL-scoped agent: agentStreamTools (null = full catalog)
      // - Universal agent (no agentId, queryMode === 'agent'): universalAgentStreamTools
      // - All other modes: no tools sent
      const toolsSel = effectiveAgentId
        ? currentState.agentStreamTools
        : isUniversalAgentMode
          ? currentState.universalAgentStreamTools
          : null;
      const toolCatalog = effectiveAgentId
        ? currentState.agentToolCatalogFullNames
        : currentState.universalAgentToolCatalogFullNames;

      /**
       * Expand null ("all tools") to catalog fullNames; explicit list passes through.
       *
       * The internal selection state uses `${instanceId}:${fullName}` composite keys so
       * multiple instances of the same toolset type can be independently selected. Strip
       * the prefix here before putting keys on the wire — the backend only understands
       * bare fullName strings.
       */
      const stripInstancePrefix = (key: string) => {
        const colon = key.indexOf(':');
        return colon >= 0 ? key.slice(colon + 1) : key;
      };
      // Deduplicate after stripping: two same-type instances share fullNames on the wire,
      // so Set removes duplicates while preserving order.
      const streamTools =
        effectiveAgentId || isUniversalAgentMode
          ? [...new Set((toolsSel === null ? [...toolCatalog] : [...toolsSel]).map(stripInstancePrefix))]
          : [];

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
                // Universal agent streams to the assistant endpoint (Option A contract):
                //   POST /api/v1/conversations[/:convId]/messages/stream with chatMode "agent:..."
                // Node.js parses chatMode and proxies to the agentIdPlaceholder path on Python,
                // where get_assistant_agent assembles a synthetic agent config from etcd toolsets.
                // `tools` in the body carries the selected fullName subset. Node.js MUST forward
                // this field to Python for per-session tool selection to take effect.
                // Do NOT route universal agent to /api/v1/agents/:id/... — that path is reserved
                // for URL-scoped agent chat. A dedicated universal-agent endpoint (Option B) may
                // replace this path in a future release.
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

