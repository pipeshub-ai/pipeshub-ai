'use client';

import { useEffect } from 'react';
import { useThreadRuntime } from '@assistant-ui/react';
import { ChatInput } from '../chat-input';
import { useChatStore, ctxKeyFromAgent } from '@/chat/store';
import { useEffectiveAgentId } from '@/chat/hooks/use-effective-agent-id';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { ChatApi } from '@/chat/api';
import { buildAssistantApiFilters, type ChatCollectionAttachment, type SearchRequest } from '@/chat/types';
import {
  isRequestCancelledError,
  isSearchNoAccessibleDocumentsNotFound,
} from '@/lib/api';

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
  const threadRuntime = useThreadRuntime();
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
      useChatStore.getState().setQueryMode('chat');
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
      console.error('Failed to fetch models for effective context', ctxKey, err);
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
    store.setSearchError(null);

    const streamFilters = buildAssistantApiFilters(store.settings.filters);
    const request: SearchRequest = {
      query,
      limit: 10,
      filters: {
        apps: streamFilters.apps,
        kb: streamFilters.kb,
      },
    };

    try {
      const response = await ChatApi.search(request, searchController.signal);
      store.setSearchResults(
        response.searchResponse.searchResults,
        response.searchId,
        query
      );
    } catch (error: unknown) {
      if (isRequestCancelledError(error)) return;
      if (isSearchNoAccessibleDocumentsNotFound(error)) {
        store.setSearchResults([], null, query);
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

  const handleSend = (message: string) => {
    if (!message.trim()) return;

    const store = useChatStore.getState();

    // Search mode: direct API call, no slots/runtime (disabled for agent-scoped chat)
    if (store.settings.mode === 'search' && !isAgentChat) {
      handleSearchSubmit(message.trim());
      return;
    }

    // ── Chat mode (existing flow) ──
    if (store.activeSlotId && store.slots[store.activeSlotId]?.isStreaming) {
      return;
    }

    // Ensure a slot exists for new chats
    let activeSlotId = store.activeSlotId;
    if (!activeSlotId) {
      activeSlotId = store.createSlot(null);
      store.setActiveSlot(activeSlotId);
      const rawAgentId =
        typeof window !== 'undefined'
          ? new URLSearchParams(window.location.search).get('agentId')
          : null;
      const agentIdFromUrl = rawAgentId?.trim() ? rawAgentId : null;
      if (agentIdFromUrl) {
        store.updateSlot(activeSlotId, {
          threadAgentId: agentIdFromUrl,
          agentStreamTools:
            store.agentStreamTools === null ? null : [...store.agentStreamTools],
        });
      }
    }

    const { settings, collectionNamesCache } = store;

    // Collections for message metadata + slot UI; `settings.filters` is not cleared on send.
    const hubApps = settings.filters.apps ?? [];
    const recordGroups = settings.filters.kb ?? [];
    const collectionsAtSendTime: ChatCollectionAttachment[] = [
      ...hubApps.map((id) => ({
        id,
        name: collectionNamesCache[id] || 'Collection',
        kind: 'collectionRoot' as const,
      })),
      ...recordGroups.map((id) => ({
        id,
        name: collectionNamesCache[id] || 'Collection',
        kind: 'recordGroup' as const,
      })),
    ];

    if (collectionsAtSendTime.length > 0) {
      store.updateSlot(activeSlotId, {
        pendingCollections: collectionsAtSendTime,
      });
    }

    // Use assistant-ui runtime to send message
    // startRun: true triggers the runtime's onNew callback
    threadRuntime.append({
      role: 'user',
      content: [{ type: 'text', text: message }],
      metadata: {
        custom: {
          collections: collectionsAtSendTime.length > 0 ? collectionsAtSendTime : undefined,
        },
      },
      startRun: true,
    });
  };

  return <ChatInput onSend={handleSend} isAgentChat={isAgentChat} agentId={effectiveAgentId} />;
}
