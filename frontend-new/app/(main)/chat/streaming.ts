/**
 * Slot-scoped SSE streaming logic.
 *
 * Extracted from the old ChatModelAdapter — this module is purely
 * imperative (no React hooks) so SSE streams can write to any slot
 * in Zustand regardless of which slot is currently active.
 *
 * Key design:
 * - `streamMessageForSlot()` handles new + existing conversations.
 * - `streamRegenerateForSlot()` handles message regeneration.
 * - rAF batching collapses high-frequency SSE chunks into one Zustand
 *   write per animation frame. Background (inactive) slot writes happen
 *   silently — no React component subscribes to those fields.
 */

import { startTransition } from 'react';
import { ChatApi, type StreamMessageCallbacks } from './api';
import { AgentsApi } from '@/app/(main)/agents/api';
import { useChatStore, ctxKeyFromAgent, getEffectiveModel } from './store';
import { debugLog } from './debug-logger';
import { loadHistoricalMessages, getThreadMessagePlainText } from './runtime';
import { i18n } from '@/lib/i18n';
import type { ThreadMessageLike } from '@assistant-ui/react';
import {
  buildStreamRequestModeFields,
  streamChatModeToAgentApiChatMode,
  type StreamChatRequest,
  type StatusMessage,
  type ModelOverride,
  type SSEConnectedEvent,
  type ChatArtifact,
  type SSEArtifactEvent,
} from './types';
import {
  buildCitationMapsFromStreaming,
} from './components/message-area/response-tabs/citations';

/**
 * If the last message is the empty placeholder assistant for an in-flight stream,
 * replace it with the error text. Otherwise append a new assistant error row.
 */
function withStreamingErrorMessage(
  currentMessages: ThreadMessageLike[],
  errorText: string
): ThreadMessageLike[] {
  const last = currentMessages[currentMessages.length - 1];
  if (last?.role === 'assistant' && getThreadMessagePlainText(last).trim() === '') {
    return [
      ...currentMessages.slice(0, -1),
      { ...last, content: [{ type: 'text' as const, text: errorText }] },
    ];
  }
  return [
    ...currentMessages,
    { role: 'assistant' as const, content: [{ type: 'text' as const, text: errorText }] },
  ];
}

function statusMessageFromConnectedEvent(data: SSEConnectedEvent): StatusMessage {
  const raw = typeof data?.message === 'string' ? data.message.trim() : '';
  const looksTechnical =
    raw.length === 0 ||
    /^sse\b/i.test(raw) ||
    /\bconnection\s+established\b/i.test(raw);
  return {
    id: 'status-connected',
    status: 'connected',
    message: looksTechnical ? 'Connected — working on your request…' : raw,
    timestamp: new Date().toISOString(),
  };
}

/** Clear partial stream output when the backend emits `restreaming` (citation verify / re-parse). */
function statusMessageRestreaming(): StatusMessage {
  return {
    id: `status-restreaming-${Date.now()}`,
    status: 'restreaming',
    message: i18n.t('chatStream.refiningResponse'),
    timestamp: new Date().toISOString(),
  };
}

interface StatusDwellScheduler {
  /** Force-apply a status immediately (bypasses dwell window). Used by restreaming. */
  applyStatus: (msg: StatusMessage | null) => void;
  /** Enqueue a status; coalesces bursts so each visible status dwells ≥ `minDwellMs`. */
  scheduleStatus: (msg: StatusMessage) => void;
  /** Drop any pending status and cancel the dwell timer. */
  cancelPendingStatus: () => void;
}

/**
 * Minimum-dwell scheduler for SSE status messages.
 *
 * Backend can emit bursts of status events (planning → executing → analyzing
 * → generating) within a few ms. Writing each one directly to the store
 * overwrites the previous before React paints, so users see statuses blink
 * past. This scheduler guarantees each visible status stays for at least
 * `minDwellMs`. Events arriving inside the window are coalesced — latest
 * wins — and flushed when the window elapses.
 */
function createStatusDwellScheduler(
  slotId: string,
  minDwellMs = 400
): StatusDwellScheduler {
  let lastStatusAt = 0;
  let statusTimer: ReturnType<typeof setTimeout> | null = null;
  let pendingStatus: StatusMessage | null = null;

  function applyStatus(msg: StatusMessage | null): void {
    lastStatusAt = Date.now();
    useChatStore.getState().updateSlot(slotId, { currentStatusMessage: msg });
  }

  function scheduleStatus(msg: StatusMessage): void {
    const elapsed = Date.now() - lastStatusAt;
    if (elapsed >= minDwellMs) {
      if (statusTimer !== null) { clearTimeout(statusTimer); statusTimer = null; }
      pendingStatus = null;
      applyStatus(msg);
      return;
    }
    pendingStatus = msg;
    if (statusTimer !== null) return;
    statusTimer = setTimeout(() => {
      statusTimer = null;
      if (pendingStatus) {
        const m = pendingStatus;
        pendingStatus = null;
        applyStatus(m);
      }
    }, minDwellMs - elapsed);
  }

  function cancelPendingStatus(): void {
    if (statusTimer !== null) { clearTimeout(statusTimer); statusTimer = null; }
    pendingStatus = null;
  }

  return { applyStatus, scheduleStatus, cancelPendingStatus };
}

/**
 * Stream a message for a specific slot.
 *
 * The function writes to `slots[slotId]` in Zustand — it does NOT
 * need the slot to be active. A background slot will accumulate
 * messages silently.
 *
 * @param slotId  — stable slot key in the store dictionary
 * @param query   — user's plain-text question
 * @param request — full StreamChatRequest (model, chatMode, filters, etc.). For **agent**
 *   streams, `ChatApi.streamMessage` always sends `filters: { apps, kb }` and `tools: [...]`
 *   — empty arrays mean no knowledge / no tools (same explicit contract).
 */
export async function streamMessageForSlot(
  slotId: string,
  query: string,
  request: StreamChatRequest
): Promise<void> {
  const store = useChatStore.getState();
  const slot = store.slots[slotId];
  if (!slot) return;

  // Create an abort controller scoped to this stream
  const abortController = new AbortController();

  // Ephemeral empty assistant so the in-progress turn has a dedicated "last
  // assistant" message. Pairs with MessageList: only the last assistant whose
  // preceding user text matches `streamingQuestion` receives live SSE props
  // (avoids `!content` false positives on older agent turns).
  const pendingAssistantId =
    typeof globalThis !== 'undefined' &&
    globalThis.crypto &&
    typeof globalThis.crypto.randomUUID === 'function'
      ? globalThis.crypto.randomUUID()
      : `asst-pending-${Date.now()}`;

  // Append user message + placeholder assistant + set streaming state atomically
  store.updateSlot(slotId, {
    isStreaming: true,
    streamingQuestion: query,
    streamingContent: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    abortController,
    threadAgentId: request.agentId ?? slot.threadAgentId ?? null,
    ...(request.agentId
      ? { agentStreamTools: [...(request.agentStreamTools ?? [])] }
      : {}),
    messages: [
      ...slot.messages,
      { role: 'user', content: [{ type: 'text', text: query }] },
      {
        role: 'assistant' as const,
        id: pendingAssistantId,
        content: [{ type: 'text' as const, text: '' }],
      },
    ],
  });

  // For new conversations, push a pending sidebar entry keyed by slotId
  const isNewConversation = slot.isTemp;
  if (isNewConversation) {
    store.addPendingConversation(slotId);
  }

  debugLog.flush('stream-started', { slotId, convId: slot.convId, isNew: isNewConversation });

  // ── Time-throttled content + citation accumulator ──────────────────
  // Flushes streamingContent + streamingCitationMaps to Zustand at most
  // once per ~16 ms (≈60 fps).
  //
  // WHY NOT requestAnimationFrame:
  // rAF is a macrotask that only runs when the browser is idle. When the
  // server sends many SSE chunks in a rapid burst (all arrive as microtasks
  // in the same event-loop turn), rafPending stays `true` through the entire
  // burst and the single rAF fires at the very end — producing one giant
  // update instead of incremental ones. A time-based throttle avoids this:
  //   • First chunk → flush immediately (content appears right away).
  //   • Subsequent chunks within 16 ms → schedule a setTimeout for the
  //     remaining window (still fires between bursts, not just at the end).
  //   • Chunks arriving ≥16 ms apart → each flushes immediately.
  //
  // BACKGROUND THROTTLING: When this slot is NOT the active (visible) one,
  // no React component subscribes to its `streamingContent` — but each
  // `updateSlot()` still creates a new `slots` reference, causing ALL
  // subscriber selectors across the app to re-evaluate synchronously.
  // With N background streams at 60 fps each, that starves the main
  // thread and breaks the active chat's scroll tracking.  To avoid this,
  // background slots flush at a much lower cadence (200 ms).
  const ACTIVE_FLUSH_MS = 16;
  const BACKGROUND_FLUSH_MS = 200;
  let accumulatedContent = '';
  let pendingCitationMaps: ReturnType<typeof buildCitationMapsFromStreaming> | null = null;
  let lastCitationKey = ''; // JSON.stringify key for dedup
  let lastFlushTime = 0;
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  let clearedStatusWhenAnswerVisible = false;

  // Minimum-dwell scheduler for SSE status messages (see
  // createStatusDwellScheduler for the rationale).
  const { applyStatus, scheduleStatus, cancelPendingStatus } =
    createStatusDwellScheduler(slotId);

  function flushContentToStore() {
    debugLog.rafFlush();
    const citationMaps = pendingCitationMaps;
    if (citationMaps) {
      pendingCitationMaps = null;
    }
    useChatStore.getState().updateSlot(slotId, {
      streamingContent: accumulatedContent,
      ...(citationMaps ? { streamingCitationMaps: citationMaps } : {}),
    });
  }

  function scheduleFlush() {
    const now = Date.now();
    // Check activity on every call — adapts immediately when user switches.
    const isActive = useChatStore.getState().activeSlotId === slotId;
    const interval = isActive ? ACTIVE_FLUSH_MS : BACKGROUND_FLUSH_MS;
    if (now - lastFlushTime >= interval) {
      // Enough time has passed — flush immediately.
      if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
      lastFlushTime = now;
      flushContentToStore();
    } else if (flushTimer === null) {
      // Within the throttle window — schedule a deferred flush.
      flushTimer = setTimeout(() => {
        flushTimer = null;
        lastFlushTime = Date.now();
        flushContentToStore();
      }, interval - (now - lastFlushTime));
    }
  }

  try {
    await ChatApi.streamMessage(request, {
      onConnected: (data) => {
        scheduleStatus(statusMessageFromConnectedEvent(data));
      },

      onRestreaming: () => {
        if (flushTimer !== null) {
          clearTimeout(flushTimer);
          flushTimer = null;
        }
        cancelPendingStatus();
        accumulatedContent = '';
        lastCitationKey = '';
        clearedStatusWhenAnswerVisible = false;
        pendingCitationMaps = null;
        useChatStore.getState().updateSlot(slotId, {
          streamingContent: '',
          streamingCitationMaps: null,
        });
        applyStatus(statusMessageRestreaming());
      },

      onStatus: (data) => {
        const statusMessage: StatusMessage = {
          id: `status-${Date.now()}`,
          status: data.status,
          message: data.message,
          timestamp: new Date().toISOString(),
        };
        scheduleStatus(statusMessage);
      },

      onChunk: (data) => {
        debugLog.chunk();
        accumulatedContent = data.accumulated;
        if (!clearedStatusWhenAnswerVisible && data.accumulated.length > 0) {
          clearedStatusWhenAnswerVisible = true;
          cancelPendingStatus();
          useChatStore.getState().updateSlot(slotId, { currentStatusMessage: null });
        }
        // Deduplicate citation maps: only stage a new maps object when
        // the serialized key changes (citations grow monotonically).
        if (data.citations && data.citations.length > 0) {
          const key = JSON.stringify(data.citations);
          if (key !== lastCitationKey) {
            lastCitationKey = key;
            pendingCitationMaps = buildCitationMapsFromStreaming(data.citations);
          }
        }
        scheduleFlush();
      },

      onArtifact: (data: SSEArtifactEvent) => {
        const artifact: ChatArtifact = {
          id: data.artifactId || `artifact-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          fileName: data.fileName,
          mimeType: data.mimeType,
          sizeBytes: data.sizeBytes ?? 0,
          downloadUrl: data.downloadUrl,
          artifactType: data.artifactType ?? 'OTHER',
          recordId: data.recordId,
        };
        const currentSlot = useChatStore.getState().slots[slotId];
        if (currentSlot) {
          useChatStore.getState().updateSlot(slotId, {
            artifacts: [...currentSlot.artifacts, artifact],
          });
        }
      },

      onComplete: (data) => {
        if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
        cancelPendingStatus();
        const conv = data.conversation as { _id?: string; id?: string };
        const newConvId = conv._id || conv.id || '';

        // Build finalized messages from API response
        const finalMessages = loadHistoricalMessages(data.conversation.messages);

        // De-prioritise the large `messages` replace so React can finish paint /
        // pointer handling first (smoother transition vs one blocking commit).
        startTransition(() => {
          useChatStore.getState().updateSlot(slotId, {
            isStreaming: false,
            streamingContent: '',
            streamingQuestion: '',
            currentStatusMessage: null,
            streamingCitationMaps: null,
            pendingCollections: [],
            artifacts: [],
            messages: finalMessages,
            hasLoaded: true,
            abortController: null,
          });
        });

        // Resolve temp → real convId
        const currentStore = useChatStore.getState();
        if (isNewConversation && newConvId) {
          currentStore.resolveSlotConvId(slotId, newConvId);
          currentStore.resolvePendingConversation(
            slotId,
            {
              id: newConvId,
              title: data.conversation.title,
              createdAt: data.conversation.createdAt,
              updatedAt: data.conversation.updatedAt,
              isShared: data.conversation.isShared,
              lastActivityAt: data.conversation.lastActivityAt,
              status: data.conversation.status,
              modelInfo: data.conversation.modelInfo,
              isOwner: true,
              sharedWith: [],
            },
            { isAgentStream: Boolean(request.agentId) }
          );
        } else if (slot.convId) {
          currentStore.moveConversationToTop(slot.convId);
        }

        debugLog.flush('stream-completed', { slotId, convId: newConvId || slot.convId });
      },

      onError: (error) => {
        if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
        cancelPendingStatus();
        console.error('[streaming] Stream error for slot', slotId, error);
        const currentMessages = useChatStore.getState().slots[slotId]?.messages ?? [];
        const err = error.message || 'An error occurred. Please try again.';
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          streamingContent: '',
          streamingQuestion: '',
          currentStatusMessage: null,
          streamingCitationMaps: null,
          pendingCollections: [],
          abortController: null,
          messages: withStreamingErrorMessage(currentMessages, err),
        });
        if (isNewConversation) {
          useChatStore.getState().clearPendingConversation(slotId);
        }
        debugLog.flush('stream-error', { slotId });
      },

      signal: abortController.signal,
    });
  } catch (error) {
    if (flushTimer !== null) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
    cancelPendingStatus();

    const aborted =
      (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError') ||
      (error instanceof Error &&
        (error.name === 'AbortError' || error.name === 'CanceledError'));

    if (aborted) {
      const cur = useChatStore.getState().slots[slotId];
      if (cur?.isStreaming) {
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          streamingContent: '',
          streamingQuestion: '',
          currentStatusMessage: null,
          streamingCitationMaps: null,
          pendingCollections: [],
          abortController: null,
        });
      }
      if (isNewConversation) {
        useChatStore.getState().clearPendingConversation(slotId);
      }
      debugLog.flush('stream-aborted', { slotId });
      return;
    }

    console.error('[streaming] Fatal error for slot', slotId, error);
    const currentMessages = useChatStore.getState().slots[slotId]?.messages ?? [];
    const errorMessage = error instanceof Error ? error.message : 'An error occurred. Please try again.';
    useChatStore.getState().updateSlot(slotId, {
      isStreaming: false,
      streamingContent: '',
      streamingQuestion: '',
      currentStatusMessage: null,
      streamingCitationMaps: null,
      pendingCollections: [],
      abortController: null,
      messages: withStreamingErrorMessage(currentMessages, errorMessage),
    });
    if (isNewConversation) {
      useChatStore.getState().clearPendingConversation(slotId);
    }
    debugLog.flush('stream-fatal-error', { slotId });
  }
}

/**
 * Regenerate a bot response for a specific slot.
 *
 * Similar to `streamMessageForSlot` but uses the regenerate endpoint
 * and replaces the last assistant message rather than appending.
 *
 * @param slotId    — stable slot key
 * @param messageId — backend _id of the bot_response to regenerate
 */
export async function streamRegenerateForSlot(
  slotId: string,
  messageId: string,
  modelOverride?: ModelOverride
): Promise<void> {
  const store = useChatStore.getState();
  const slot = store.slots[slotId];
  if (!slot || !slot.convId) return;

  // Resolve model: explicit override → context-scoped selection/default.
  // Context is the slot's own agent (so regenerate for an agent thread
  // always picks from that agent's models, never leaks assistant choices).
  const regenCtxKey = ctxKeyFromAgent(slot.threadAgentId ?? null);
  const resolvedModel: ModelOverride =
    modelOverride
      ?? getEffectiveModel(regenCtxKey)
      ?? { modelKey: '', modelName: '', modelFriendlyName: '' };

  const abortController = new AbortController();

  store.updateSlot(slotId, {
    isStreaming: true,
    regenerateMessageId: messageId,
    streamingContent: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    abortController,
  });

  debugLog.flush('regenerate-started', { slotId, messageId });

  // ── Time-throttled content + citation accumulator (same as streamMessageForSlot) ──
  const ACTIVE_FLUSH_MS = 16;
  const BACKGROUND_FLUSH_MS = 200;
  let accumulatedContent = '';
  let pendingCitationMaps: ReturnType<typeof buildCitationMapsFromStreaming> | null = null;
  let lastCitationKey = '';
  let lastFlushTime = 0;
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  let clearedStatusWhenAnswerVisible = false;

  // Minimum-dwell scheduler for SSE status messages (see
  // createStatusDwellScheduler for the rationale).
  const { applyStatus, scheduleStatus, cancelPendingStatus } =
    createStatusDwellScheduler(slotId);

  function flushContentToStore() {
    debugLog.rafFlush();
    const citationMaps = pendingCitationMaps;
    if (citationMaps) {
      pendingCitationMaps = null;
    }
    useChatStore.getState().updateSlot(slotId, {
      streamingContent: accumulatedContent,
      ...(citationMaps ? { streamingCitationMaps: citationMaps } : {}),
    });
  }

  function scheduleFlush() {
    const now = Date.now();
    const isActive = useChatStore.getState().activeSlotId === slotId;
    const interval = isActive ? ACTIVE_FLUSH_MS : BACKGROUND_FLUSH_MS;
    if (now - lastFlushTime >= interval) {
      if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
      lastFlushTime = now;
      flushContentToStore();
    } else if (flushTimer === null) {
      flushTimer = setTimeout(() => {
        flushTimer = null;
        lastFlushTime = Date.now();
        flushContentToStore();
      }, interval - (now - lastFlushTime));
    }
  }

  const rawAgentIdFromUrl =
    typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('agentId') : null;
  const agentIdFromUrl = rawAgentIdFromUrl?.trim() ? rawAgentIdFromUrl : null;
  const slotAgentId = slot.threadAgentId?.trim() || null;
  const threadAgentId = slotAgentId ?? agentIdFromUrl;
  /** Which API we use for reload — frozen at regen start (URL may change before `complete`) */
  const reloadViaAgentId = threadAgentId;

  const regenerateCallbacks: StreamMessageCallbacks = {
    onConnected: (data) => {
      scheduleStatus(statusMessageFromConnectedEvent(data));
    },

    onRestreaming: () => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      cancelPendingStatus();
      accumulatedContent = '';
      lastCitationKey = '';
      clearedStatusWhenAnswerVisible = false;
      pendingCitationMaps = null;
      useChatStore.getState().updateSlot(slotId, {
        streamingContent: '',
        streamingCitationMaps: null,
      });
      applyStatus(statusMessageRestreaming());
    },

    onStatus: (data) => {
      scheduleStatus({
        id: `status-${Date.now()}`,
        status: data.status,
        message: data.message,
        timestamp: new Date().toISOString(),
      });
    },

    onChunk: (data) => {
      debugLog.chunk();
      accumulatedContent = data.accumulated;
      if (!clearedStatusWhenAnswerVisible && data.accumulated.length > 0) {
        clearedStatusWhenAnswerVisible = true;
        cancelPendingStatus();
        useChatStore.getState().updateSlot(slotId, { currentStatusMessage: null });
      }
      if (data.citations && data.citations.length > 0) {
        const key = JSON.stringify(data.citations);
        if (key !== lastCitationKey) {
          lastCitationKey = key;
          pendingCitationMaps = buildCitationMapsFromStreaming(data.citations);
        }
      }
      scheduleFlush();
    },

    onComplete: async () => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      cancelPendingStatus();
      try {
        const messages = reloadViaAgentId
          ? (await AgentsApi.fetchAgentConversation(reloadViaAgentId, slot.convId!)).messages
          : (await ChatApi.fetchConversation(slot.convId!)).messages;
        const finalMessages = loadHistoricalMessages(messages);

        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          regenerateMessageId: null,
          streamingContent: '',
          currentStatusMessage: null,
          streamingCitationMaps: null,
          messages: finalMessages,
          abortController: null,
        });
        debugLog.flush('regenerate-completed', { slotId, messageId });
      } catch (err) {
        console.error('[streaming] Failed to reload after regenerate:', err);
        useChatStore.getState().updateSlot(slotId, {
          isStreaming: false,
          regenerateMessageId: null,
          streamingContent: '',
          currentStatusMessage: null,
          streamingCitationMaps: null,
          abortController: null,
        });
        debugLog.flush('regenerate-reload-error', { slotId });
      }
    },

    onError: (error: Error) => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      cancelPendingStatus();
      console.error('[streaming] Regenerate error for slot', slotId, error);
      useChatStore.getState().updateSlot(slotId, {
        isStreaming: false,
        regenerateMessageId: null,
        streamingContent: '',
        currentStatusMessage: null,
        streamingCitationMaps: null,
        abortController: null,
      });
      debugLog.flush('regenerate-error', { slotId });
    },

    signal: abortController.signal,
  };

  try {
    if (threadAgentId && slotAgentId !== threadAgentId) {
      useChatStore.getState().updateSlot(slotId, { threadAgentId });
    }
    if (threadAgentId) {
      const { chatMode } = buildStreamRequestModeFields(store.settings);
      const agentApiChatMode = streamChatModeToAgentApiChatMode(chatMode);
      await ChatApi.streamAgentRegenerate(
        threadAgentId,
        slot.convId,
        messageId,
        regenerateCallbacks,
        {
          modelKey: resolvedModel.modelKey.trim(),
          modelName: resolvedModel.modelName || resolvedModel.modelKey,
          modelProvider: resolvedModel.modelProvider ?? 'openAI',
          chatMode: agentApiChatMode,
        }
      );
    } else {
      const { chatMode } = buildStreamRequestModeFields(store.settings);
      await ChatApi.streamRegenerate(slot.convId, messageId, regenerateCallbacks, {
        modelKey: resolvedModel.modelKey,
        modelName: resolvedModel.modelName,
        modelFriendlyName: resolvedModel.modelFriendlyName,
        chatMode,
        filters: store.settings.filters,
      });
    }
  } catch (error) {
    if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
    cancelPendingStatus();
    console.error('[streaming] Fatal regenerate error for slot', slotId, error);
    useChatStore.getState().updateSlot(slotId, {
      isStreaming: false,
      regenerateMessageId: null,
      streamingContent: '',
      currentStatusMessage: null,
      streamingCitationMaps: null,
      abortController: null,
    });
    debugLog.flush('regenerate-fatal-error', { slotId });
  }
}

/**
 * Cancel the active stream for a slot by aborting its AbortController.
 */
export function cancelStreamForSlot(slotId: string): void {
  const store = useChatStore.getState();
  const slot = store.slots[slotId];
  if (!slot) return;

  slot.abortController?.abort();
  store.updateSlot(slotId, {
    isStreaming: false,
    streamingContent: '',
    streamingQuestion: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    abortController: null,
    regenerateMessageId: null,
  });
  if (slot.isTemp) {
    store.clearPendingConversation(slotId);
  }
  debugLog.flush('stream-cancelled', { slotId });
}
