'use client';

import { useCallback, useEffect } from 'react';
import { useAui } from '@assistant-ui/react';
import { ChatInput } from '../chat-input';
import type { MentionNode } from '@/chat/utils/node-mentions';
import { useChatStore, ctxKeyFromAgent } from '@/chat/store';
import { useEffectiveAgentId } from '@/chat/hooks/use-effective-agent-id';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { ChatApi } from '@/chat/api';
import { useComposerPrefill } from '@/chat/commands';
import {
  buildAssistantApiFilters,
  type AttachmentRef,
  type SearchRequest,
} from '@/chat/types';
import {
  isRequestCancelledError,
  isSearchNoAccessibleDocumentsNotFound,
} from '@/lib/api';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { InteractionWaitingBar } from '../interaction-waiting-bar';
import { JobAskFloat } from '../job-ask/job-ask-float';
import { sendChatMessage } from '../../send-chat-message';
import { i18n } from '@/lib/i18n';
import { readAccessNotices } from '@/app/components/feedback/access-notice';

// Module-level abort controller for cancelling in-flight searches
let currentSearchAbort: AbortController | null = null;
/** Increments on each submit so superseded requests never clear loading for a newer search. */
let searchSubmitGeneration = 0;
let lastEffectiveAgentIdForQueryMode: string | null = null;

/**
 * Wrapper component that connects ChatInput to assistant-ui runtime.
 * Must be used inside AssistantRuntimeProvider.
 */
export function ChatInputWrapper() {
  // "Try it" from the skills editor (SLASH1): taken once, so a later visit to the chat starts empty.
  const prefill = useComposerPrefill((s) => s.prefill);
  useEffect(() => {
    if (prefill) useComposerPrefill.setState({ prefill: null });
  }, [prefill]);
  const aui = useAui();
  const effectiveAgentId = useEffectiveAgentId();
  const isAgentChat = Boolean(effectiveAgentId);

  useEffect(() => {
    if (effectiveAgentId) {
      lastEffectiveAgentIdForQueryMode = effectiveAgentId;
      const store = useChatStore.getState();
      store.setQueryMode('agent');
      if (store.settings.mode === 'search') {
        store.setMode('chat');
        store.clearSearchResults();
      }
      return;
    }

    if (lastEffectiveAgentIdForQueryMode !== null) {
      lastEffectiveAgentIdForQueryMode = null;
      useChatStore.getState().setQueryMode('agent');
    }
  }, [effectiveAgentId]);

  // Make sure models for the EFFECTIVE context (URL or slot agent) are loaded
  // and validated, regardless of which URL the page was opened on. This keeps
  // the pill + submit in sync when the active slot carries an agent that the
  // URL doesn't reflect (e.g. navigating into an existing agent conversation).
  // The fetch util dedupes so this is cheap when page.tsx already ran.
  useEffect(() => {
    const ctxKey = ctxKeyFromAgent(effectiveAgentId);
    fetchModelsForContext(ctxKey).catch((err) => {
      if (useServicesHealthStore.getState().apiServerReachable) {
        console.error('Failed to fetch models for effective context', ctxKey, err);
      }
    });
  }, [effectiveAgentId]);

  const handleSearchSubmit = async (query: string) => {
    const store = useChatStore.getState();

    // Cancel any in-flight search
    if (currentSearchAbort) {
      currentSearchAbort.abort();
    }
    const myGeneration = ++searchSubmitGeneration;
    const searchController = new AbortController();
    currentSearchAbort = searchController;

    store.setIsSearching(true);
    // The query first, so the heading shows while loading and a closed search can be told apart.
    store.setSearchResults([], null, query);

    const request: SearchRequest = {
      query,
      limit: 10,
      filters: buildAssistantApiFilters(store.settings.filters),
    };

    // Closing search (clearSearchResults) or a newer search makes this answer stale.
    const isStale = () =>
      myGeneration !== searchSubmitGeneration || useChatStore.getState().searchQuery !== query;

    try {
      const response = await ChatApi.search(request, searchController.signal);
      if (isStale()) return;
      store.setSearchResults(
        response.searchResponse.searchResults,
        response.searchId,
        query,
        response,
      );
    } catch (error: unknown) {
      if (isRequestCancelledError(error) || isStale()) return;
      if (isSearchNoAccessibleDocumentsNotFound(error)) {
        // Keep the server's reason ("No documents are available…") for the empty state.
        store.setSearchResults([], null, query, {
          message: (error as Error).message,
          // Codes say whether "nothing" is because a source is still syncing.
          searchResponse: { searchResults: [], accessNotices: (error as { details?: { accessNotices?: unknown } }).details?.accessNotices },
        });
        return;
      }
      // "We couldn't check which documents you have access to": said in the reader's language, not the server's.
      const noticed = readAccessNotices((error as { details?: { accessNotices?: unknown } }).details?.accessNotices);
      if (noticed.some((n) => n.code === 'check_unavailable')) {
        store.setSearchError(i18n.t('accessNotice.check_unavailable'));
        return;
      }
      store.setSearchError((error as Error)?.message || 'Search failed');
    } finally {
      if (currentSearchAbort === searchController) {
        currentSearchAbort = null;
      }
      if (myGeneration === searchSubmitGeneration) {
        store.setIsSearching(false);
      }
    }
  };

  /**
   * Per-file upload, fired by `ChatInput` the moment a chip is added to the
   * composer. Wraps the batch-upload endpoint with a single file in the
   * FormData so we don't need a new backend route. `signal` is forwarded so
   * `ChatInput` can abort when the user removes a chip mid-flight.
   */
  const handleUploadFile = useCallback(
    async (file: File, signal: AbortSignal): Promise<AttachmentRef> => {
      const store = useChatStore.getState();
      const slot = store.activeSlotId ? store.slots[store.activeSlotId] : null;
      const refs = await ChatApi.uploadAttachments([file], {
        agentId: effectiveAgentId,
        conversationId: slot?.convId ?? null,
        signal,
      });
      const ref = refs[0];
      if (!ref) throw new Error("The upload didn't finish. Please attach the file again.");
      return ref;
    },
    [effectiveAgentId],
  );

  const handleDeleteFile = useCallback(
    (recordId: string) => {
      // Fire and forget — must never block the UI.
      ChatApi.deleteAttachment(recordId, { agentId: effectiveAgentId }).catch(() => {
        // Swallow silently: an orphan record is acceptable; blocking the UI is not.
      });
    },
    [effectiveAgentId],
  );

  const handleSend = async (message: string, attachments?: AttachmentRef[], mentions?: MentionNode[]) => {
    if (!message.trim() && (!attachments || attachments.length === 0)) return;

    const store = useChatStore.getState();

    // Search mode: direct API call, no slots/runtime (disabled for agent-scoped chat)
    // Attachments are not supported in search mode — silently ignored.
    if (store.settings.mode === 'search' && !isAgentChat) {
      if (message.trim()) handleSearchSubmit(message.trim());
      return;
    }

    sendChatMessage(aui, message, attachments, mentions);
  };

  return (
    <>
      <JobAskFloat />
      <InteractionWaitingBar />
      <ChatInput
        onSend={handleSend}
        onUploadFile={handleUploadFile}
        onDeleteFile={handleDeleteFile}
        isAgentChat={isAgentChat}
        agentId={effectiveAgentId}
        prefill={prefill}
        persistDraft
      />
    </>
  );
}
