'use client';

import { useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { useThreadRuntime } from '@assistant-ui/react';
import { ChatInput } from '../chat-input';
import { useChatStore } from '@/chat/store';
import { ChatApi } from '@/chat/api';
import type { SearchRequest } from '@/chat/types';

// Module-level abort controller for cancelling in-flight searches
let currentSearchAbort: AbortController | null = null;

/**
 * Wrapper component that connects ChatInput to assistant-ui runtime.
 * Must be used inside AssistantRuntimeProvider.
 */
export function ChatInputWrapper() {
  const threadRuntime = useThreadRuntime();
  const searchParams = useSearchParams();
  const rawAgentId = searchParams.get('agentId');
  const slotAgentId = useChatStore((s) => {
    const sid = s.activeSlotId;
    return sid ? (s.slots[sid]?.threadAgentId?.trim() ?? '') : '';
  });
  // Determine effective agent ID (prefer slot's agent ID, fallback to URL)
  const effectiveAgentId = slotAgentId || (rawAgentId?.trim() ? rawAgentId : null);
  const isAgentChat = Boolean(effectiveAgentId);

  useEffect(() => {
    if (!isAgentChat) return;
    const store = useChatStore.getState();
    store.setQueryMode('agent');
    if (store.settings.mode === 'search') {
      store.setMode('chat');
      store.clearSearchResults();
    }
  }, [isAgentChat]);

  useEffect(() => {
    return () => {
      if (currentSearchAbort) {
        currentSearchAbort.abort();
        currentSearchAbort = null;
      }
    };
  }, []);

  const handleSearchSubmit = async (query: string) => {
    const store = useChatStore.getState();

    // Cancel any in-flight search
    if (currentSearchAbort) {
      currentSearchAbort.abort();
    }
    currentSearchAbort = new AbortController();

    store.setIsSearching(true);
    store.setSearchError(null);

    const kbFilter = store.settings.filters.kb;
    const request: SearchRequest = {
      query,
      limit: 10,
      filters: {
        departments: [],
        moduleIds: [],
        appSpecificRecordTypes: [],
        apps: [...store.settings.filters.apps, ...kbFilter],
        kb: [],
      },
    };

    try {
      const response = await ChatApi.search(request, currentSearchAbort.signal);
      store.setSearchResults(
        response.searchResponse.searchResults,
        response.searchId,
        query
      );
    } catch (error: unknown) {
      if ((error as { name?: string })?.name === 'AbortError' || (error as { name?: string })?.name === 'CanceledError') return;
      store.setSearchError((error as Error)?.message || 'Search failed');
    } finally {
      store.setIsSearching(false);
      currentSearchAbort = null;
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
          agentStreamTools: [...store.agentStreamTools],
        });
      }
    }

    const { settings, setFilters, collectionNamesCache } = store;

    // Snapshot the selected collections before clearing — these get attached
    // to the user message metadata so ChatResponse can render collection cards.
    const collectionsAtSendTime = settings.filters.kb.map((id) => ({
      id,
      name: collectionNamesCache[id] || 'Collection',
    }));

    // Store collections in the slot for the streaming UI (temp message)
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

    // Clear KB filters after send so the cards disappear from the input area
    if (collectionsAtSendTime.length > 0) {
      setFilters({ ...settings.filters, kb: [] });
    }
  };

  return <ChatInput onSend={handleSend} isAgentChat={isAgentChat} agentId={effectiveAgentId} />;
}
