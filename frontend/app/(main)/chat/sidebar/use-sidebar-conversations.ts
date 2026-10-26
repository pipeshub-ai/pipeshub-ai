'use client';

import { useCallback, useEffect } from 'react';
import { ChatApi } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { SIDEBAR_CONVERSATIONS_PAGE_SIZE } from '../constants';

/** Loads Recents for whatever page is showing ChatSidebar (chat, artifacts, …). */
export function useSidebarConversations() {
  const setConversations = useChatStore((s) => s.setConversations);
  const setSharedConversations = useChatStore((s) => s.setSharedConversations);
  const setIsConversationsLoading = useChatStore((s) => s.setIsConversationsLoading);
  const setConversationsError = useChatStore((s) => s.setConversationsError);
  const setPagination = useChatStore((s) => s.setPagination);
  const setSharedPagination = useChatStore((s) => s.setSharedPagination);
  const conversationsVersion = useChatStore((s) => s.conversationsVersion);

  const loadConversations = useCallback(async () => {
    setIsConversationsLoading(true);
    setConversationsError(null);

    try {
      const [owned, shared] = await Promise.all([
        ChatApi.fetchConversations(1, SIDEBAR_CONVERSATIONS_PAGE_SIZE, {
          source: 'owned',
        }),
        ChatApi.fetchConversations(1, SIDEBAR_CONVERSATIONS_PAGE_SIZE, {
          source: 'shared',
        }),
      ]);
      setConversations(owned.conversations);
      setSharedConversations(shared.conversations);
      setPagination(owned.pagination);
      setSharedPagination(shared.pagination);
    } catch (error) {
      if (useServicesHealthStore.getState().apiServerReachable) {
        console.error('Failed to fetch conversations:', error);
        setConversationsError(
          error instanceof Error ? error.message : 'Failed to fetch conversations',
        );
      }
    } finally {
      setIsConversationsLoading(false);
    }
  }, [
    setConversations,
    setSharedConversations,
    setIsConversationsLoading,
    setConversationsError,
    setPagination,
    setSharedPagination,
  ]);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    if (conversationsVersion > 0) {
      void loadConversations();
    }
  }, [conversationsVersion, loadConversations]);
}
