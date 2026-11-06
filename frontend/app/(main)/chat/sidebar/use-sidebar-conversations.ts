'use client';

import { useEffect } from 'react';
import useSWR, { mutate, preload } from 'swr';
import { ChatApi } from '@/chat/api';
import { useChatStore, type SidebarConversationLists } from '@/chat/store';
import { getCurrentUserId } from '@/chat/utils/current-user-id';
import { startListRead, withHeldTitles } from '@/chat/utils/held-titles';
import { useAuthStore } from '@/config';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import {
  useFeatureFlagsStore,
  selectFeatureFlagsLoaded,
  selectProjectsEnabled,
} from '@/lib/store/feature-flags-store';
import { revalidateSwrNamespace, SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import { clearSessionCache } from '@/lib/api/session-cache';
import { SIDEBAR_CONVERSATIONS_PAGE_SIZE } from '../constants';

type SidebarConversationsKey = readonly [
  typeof SWR_NS.conversationLists,
  'sidebar',
  { limit: number; excludeProjectChats: boolean },
];

const sidebarKey = (excludeProjectChats: boolean): SidebarConversationsKey => [
  SWR_NS.conversationLists,
  'sidebar',
  { limit: SIDEBAR_CONVERSATIONS_PAGE_SIZE, excludeProjectChats },
];

async function fetchSidebarConversations([, , { limit, excludeProjectChats }]: SidebarConversationsKey): Promise<SidebarConversationLists> {
  const read = startListRead();
  // The sidebar shows its own "Failed to load"; a background refresh must not toast.
  const [owned, shared] = await Promise.all([
    ChatApi.fetchConversations(1, limit, { source: 'owned', excludeProjectChats, suppressErrorToast: true }),
    ChatApi.fetchConversations(1, limit, { source: 'shared', suppressErrorToast: true }),
  ]);
  // A rename made in this tab while the read was out keeps its new title.
  return {
    owned: { ...owned, conversations: withHeldTitles(owned.conversations, read) },
    shared: { ...shared, conversations: withHeldTitles(shared.conversations, read) },
    excludeProjectChats,
  };
}

/**
 * Loads Recents for whatever page is showing ChatSidebar (chat, artifacts, …).
 *
 * The chat store keeps the working copy that streaming and row actions edit, so
 * only a fresh response replaces it (`onSuccess`). Cached data seen on a remount
 * is older than the store and is not written back, except when it belongs to the
 * other Projects filter than the one the store holds. `enabled` false (an agent's
 * or a project's chats show instead): nothing loads until it is true again.
 */
export function useSidebarConversations(enabled = true): void {
  const setSidebarLists = useChatStore((s) => s.setSidebarLists);
  const setIsConversationsLoading = useChatStore((s) => s.setIsConversationsLoading);
  const setConversationsError = useChatStore((s) => s.setConversationsError);
  // Waiting for the flags fetches once, with the right filter.
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  // With Projects off a project chat has nowhere else to show, so list it here.
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);

  // Sign-out clears every rail list, also when this rail shows an agent's or a project's chats and never loads these.
  useEffect(() => watchSidebarSession(), []);

  const key: SidebarConversationsKey | null = enabled && flagsLoaded ? sidebarKey(projectsEnabled) : null;

  const { data, error, isLoading, isValidating } = useSWR(key, fetchSidebarConversations, {
    ...SWR_POLICY.list30s,
    onSuccess: setSidebarLists,
  });

  useEffect(() => {
    if (data && useChatStore.getState().sidebarListsExcludeProjectChats !== data.excludeProjectChats) {
      setSidebarLists(data);
    }
  }, [data, setSidebarLists]);

  useEffect(() => {
    if (!enabled) return;
    // Lists already on screen stay there while they are read again; only a rail with nothing loaded waits on a skeleton.
    const waiting = !flagsLoaded || isLoading;
    if (!waiting || useChatStore.getState().conversationsLoadedAt === null) setIsConversationsLoading(waiting);
  }, [enabled, flagsLoaded, isLoading, setIsConversationsLoading]);

  useEffect(() => {
    if (error) console.error('Failed to fetch conversations:', error);
  }, [error]);

  // "Failed to load" replaces the whole section, so it is shown only when there
  // is nothing to show instead: a failed background refresh keeps the list, and
  // an old error stays hidden while a new request runs.
  const failed = Boolean(error) && !data && !isValidating;
  useEffect(() => {
    if (!enabled) return;
    if (!failed) {
      setConversationsError(null);
      return;
    }
    if (!useServicesHealthStore.getState().apiServerReachable) return;
    setConversationsError(error instanceof Error ? error.message : 'Failed to fetch conversations');
  }, [enabled, failed, error, setConversationsError]);
}

/** Loads the rail's lists now; a rail that mounts next uses this answer instead of asking again. */
export async function loadSidebarConversations(): Promise<void> {
  const key = sidebarKey(selectProjectsEnabled(useFeatureFlagsStore.getState()));
  useChatStore.getState().setSidebarLists(await preload(key, fetchSidebarConversations));
}

/**
 * Reloads the rail's lists when one of them may have changed (a chat shared with the user, a chat that became shared
 * or private). Both lists come in one cached read, so both are asked again.
 */
export function refreshSidebarList(_list: 'owned' | 'shared'): Promise<void> {
  revalidateSwrNamespace(SWR_NS.conversationLists);
  return Promise.resolve();
}

let watchedUserId: string | null | undefined;

/**
 * Someone else signing in without a sign-out first (the desktop app does not reload the page) drops the last
 * person's lists and cached reads, so the next person never sees their chats. Installed when a rail mounts.
 */
export function watchSidebarSession(): void {
  if (watchedUserId !== undefined || typeof window === 'undefined') return;
  watchedUserId = getCurrentUserId();
  useAuthStore.subscribe(() => {
    const userId = getCurrentUserId();
    if (userId === watchedUserId) return;
    const hadUser = watchedUserId !== null;
    watchedUserId = userId;
    // A first sign-in has nothing of anyone's to drop.
    if (hadUser) clearSessionCache();
  });
}

/** Tests only: forget every cached read and what the rails show. */
export function resetSidebarConversationsForTests(): void {
  void mutate(() => true, undefined, { revalidate: false });
  clearSessionCache();
}
