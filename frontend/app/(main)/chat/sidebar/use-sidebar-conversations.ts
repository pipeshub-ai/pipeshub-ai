'use client';

import { useEffect } from 'react';
import useSWR from 'swr';
import { ChatApi } from '@/chat/api';
import { useChatStore, type SidebarConversationLists } from '@/chat/store';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import {
  useFeatureFlagsStore,
  selectFeatureFlagsLoaded,
  selectProjectsEnabled,
} from '@/lib/store/feature-flags-store';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import { SIDEBAR_CONVERSATIONS_PAGE_SIZE } from '../constants';

type SidebarConversationsKey = readonly [
  typeof SWR_NS.conversationLists,
  'sidebar',
  { limit: number; excludeProjectChats: boolean },
];

async function fetchSidebarConversations([, , { limit, excludeProjectChats }]: SidebarConversationsKey): Promise<SidebarConversationLists> {
  // The sidebar shows its own "Failed to load"; a background refresh must not toast.
  const [owned, shared] = await Promise.all([
    ChatApi.fetchConversations(1, limit, { source: 'owned', excludeProjectChats, suppressErrorToast: true }),
    ChatApi.fetchConversations(1, limit, { source: 'shared', suppressErrorToast: true }),
  ]);
  return { owned, shared, excludeProjectChats };
}

/**
 * Loads Recents for whatever page is showing ChatSidebar (chat, artifacts, …).
 *
 * The chat store keeps the working copy that streaming and row actions edit, so
 * only a fresh response replaces it (`onSuccess`). Cached data seen on a remount
 * is older than the store and is not written back, except when it belongs to the
 * other Projects filter than the one the store holds.
 */
export function useSidebarConversations(): void {
  const setSidebarLists = useChatStore((s) => s.setSidebarLists);
  const setIsConversationsLoading = useChatStore((s) => s.setIsConversationsLoading);
  const setConversationsError = useChatStore((s) => s.setConversationsError);
  // Waiting for the flags fetches once, with the right filter.
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  // With Projects off a project chat has nowhere else to show, so list it here.
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);

  const key: SidebarConversationsKey | null = flagsLoaded
    ? [
        SWR_NS.conversationLists,
        'sidebar',
        { limit: SIDEBAR_CONVERSATIONS_PAGE_SIZE, excludeProjectChats: projectsEnabled },
      ]
    : null;

  const { data, error, isLoading } = useSWR(key, fetchSidebarConversations, {
    ...SWR_POLICY.list30s,
    onSuccess: setSidebarLists,
  });

  useEffect(() => {
    if (data && useChatStore.getState().sidebarListsExcludeProjectChats !== data.excludeProjectChats) {
      setSidebarLists(data);
    }
  }, [data, setSidebarLists]);

  useEffect(() => {
    setIsConversationsLoading(!flagsLoaded || isLoading);
  }, [flagsLoaded, isLoading, setIsConversationsLoading]);

  useEffect(() => {
    if (!error) {
      setConversationsError(null);
      return;
    }
    if (!useServicesHealthStore.getState().apiServerReachable) return;
    console.error('Failed to fetch conversations:', error);
    setConversationsError(error instanceof Error ? error.message : 'Failed to fetch conversations');
  }, [error, setConversationsError]);
}
