'use client';

import { useEffect } from 'react';
import { ChatApi } from '@/chat/api';
import { useChatStore } from '@/chat/store';
import { getCurrentUserId } from '@/chat/utils/current-user-id';
import { startListRead, withHeldTitles } from '@/chat/utils/held-titles';
import { useAuthStore } from '@/config';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { SIDEBAR_CONVERSATIONS_PAGE_SIZE, SIDEBAR_LISTS_STALE_MS } from '../constants';
import { anyWorking, onJobListsRefresh, useJobListsRefresh } from '@/lib/harness-jobs/swr-keys';

let inFlight: Promise<void> | null = null;
/** Something changed after the load in flight started, so it runs once more when that one ends. */
let loadAgain = false;
/** The `conversationsVersion` the lists on screen were loaded at; a bump since means they are behind. */
let loadedVersion = -1;
/** Whether Your chats was last read with project chats left out (Projects on); null before it was read. */
let ownedReadWithProjects: boolean | null = null;

/** Longest the first read of Your chats waits for the feature flags before going with Projects off. */
const FLAGS_WAIT_MS = 3000;

/**
 * Whether Projects is on, once the flags are known: Your chats leaves project chats out while it is, so reading it
 * before they are known meant reading it twice on every reload. Goes with what it has after FLAGS_WAIT_MS.
 */
function projectsEnabledOnceKnown(): Promise<boolean> {
  return new Promise((resolve) => {
    const done = () => {
      stop();
      clearTimeout(timer);
      resolve(selectProjectsEnabled(useFeatureFlagsStore.getState()));
    };
    const stop = useFeatureFlagsStore.subscribe((state) => {
      if (state.flags !== null) done();
    });
    const timer = setTimeout(done, FLAGS_WAIT_MS);
  });
}

/** Whether the rail's lists are recent enough to show without asking the server again. */
export function sidebarListsAreFresh(now = Date.now()): boolean {
  const { conversationsLoadedAt, conversationsVersion } = useChatStore.getState();
  return (
    conversationsLoadedAt !== null &&
    now - conversationsLoadedAt < SIDEBAR_LISTS_STALE_MS &&
    loadedVersion === conversationsVersion
  );
}

async function loadListsOnce(): Promise<void> {
  watchSidebarSession();
  const store = useChatStore.getState();
  // Only a rail with nothing loaded waits on a skeleton; any later load swaps the rows in place, quietly.
  const first = store.conversationsLoadedAt === null;
  const version = store.conversationsVersion;
  const userId = getCurrentUserId();
  const quiet = first ? {} : { suppressErrorToast: true };
  const read = startListRead();
  if (first) store.setIsConversationsLoading(true);
  try {
    // With Projects off a project chat has nowhere else to show, so list it here.
    let projectsEnabled = false;
    const readOwned = (enabled: boolean) => {
      projectsEnabled = enabled;
      return ChatApi.fetchConversations(1, SIDEBAR_CONVERSATIONS_PAGE_SIZE, {
        source: 'owned',
        excludeProjectChats: enabled,
        ...quiet,
      });
    };
    const flags = useFeatureFlagsStore.getState();
    const [owned, shared] = await Promise.all([
      flags.flags !== null ? readOwned(selectProjectsEnabled(flags)) : projectsEnabledOnceKnown().then(readOwned),
      ChatApi.fetchConversations(1, SIDEBAR_CONVERSATIONS_PAGE_SIZE, {
        source: 'shared',
        ...quiet,
      }),
    ]);
    // Signed out, or someone else signed in, while this was in flight: these are not their chats.
    if (getCurrentUserId() !== userId) return;
    loadedVersion = version;
    ownedReadWithProjects = projectsEnabled;
    useChatStore.setState({
      conversations: withHeldTitles(owned.conversations, read),
      sharedConversations: withHeldTitles(shared.conversations, read),
      pagination: owned.pagination,
      sharedPagination: shared.pagination,
      conversationsError: null,
      conversationsLoadedAt: Date.now(),
    });
  } catch (error) {
    // A refresh that fails keeps the rows on screen; only a rail with nothing to show says it failed.
    if (first && getCurrentUserId() === userId && useServicesHealthStore.getState().apiServerReachable) {
      console.error('Failed to fetch conversations:', error);
      useChatStore
        .getState()
        .setConversationsError(error instanceof Error ? error.message : 'Failed to fetch conversations');
    }
  } finally {
    // Only this person's load ends their skeleton: the last person's, landing late, leaves the next person's on. A
    // sign-out already turned it off.
    if (first && getCurrentUserId() === userId) useChatStore.getState().setIsConversationsLoading(false);
  }
}

/**
 * Loads the rail's owned and shared lists, one pair of requests at a time. `again`: something changed since a load in
 * flight started (a notice, a bump), so it runs once more after that one instead of being dropped.
 */
export function loadSidebarConversations({ again = false }: { again?: boolean } = {}): Promise<void> {
  if (inFlight) {
    if (again) loadAgain = true;
    return inFlight;
  }
  const flight = (async () => {
    try {
      do {
        loadAgain = false;
        await loadListsOnce();
        // A change of person drops this flight; a load started since is not this one's to run again or to clear.
      } while (loadAgain && inFlight === flight);
    } finally {
      if (inFlight === flight) inFlight = null;
    }
  })();
  inFlight = flight;
  return flight;
}

type SidebarList = 'owned' | 'shared';
const listFlights: Record<SidebarList, { promise: Promise<void> | null; again: boolean }> = {
  owned: { promise: null, again: false },
  shared: { promise: null, again: false },
};

async function loadOneList(list: SidebarList): Promise<void> {
  watchSidebarSession();
  const userId = getCurrentUserId();
  const projectsEnabled = selectProjectsEnabled(useFeatureFlagsStore.getState());
  const read = startListRead();
  try {
    const result = await ChatApi.fetchConversations(1, SIDEBAR_CONVERSATIONS_PAGE_SIZE, {
      source: list,
      ...(list === 'owned' ? { excludeProjectChats: projectsEnabled } : {}),
      suppressErrorToast: true,
    });
    if (getCurrentUserId() !== userId) return;
    if (list === 'owned') ownedReadWithProjects = projectsEnabled;
    // The server lists a chat in one of the two only: one that moved here leaves the other list.
    const ids = new Set(result.conversations.map((c) => c.id));
    const rows = withHeldTitles(result.conversations, read);
    useChatStore.setState((state) =>
      list === 'owned'
        ? {
            conversations: rows,
            pagination: result.pagination,
            sharedConversations: state.sharedConversations.filter((c) => !ids.has(c.id)),
          }
        : {
            sharedConversations: rows,
            sharedPagination: result.pagination,
            conversations: state.conversations.filter((c) => !ids.has(c.id)),
          },
    );
  } catch {
    // A refresh that fails keeps the rows on screen.
  }
}

/**
 * Reloads one of the rail's lists, when only it can have changed (a chat shared with the user, a chat that became
 * shared or private): one request, one at a time per list. Nothing loaded yet: the rail's first load covers it.
 */
export function refreshSidebarList(list: SidebarList): Promise<void> {
  if (useChatStore.getState().conversationsLoadedAt === null) return Promise.resolve();
  // A full load in flight may have read the list before the change: it runs once more and covers it.
  if (inFlight) {
    loadAgain = true;
    return inFlight;
  }
  const flight = listFlights[list];
  if (flight.promise) {
    flight.again = true;
    return flight.promise;
  }
  const promise = (async () => {
    try {
      do {
        flight.again = false;
        await loadOneList(list);
        // A change of person drops this flight; a refresh started since is not this one's to run again or to clear.
      } while (flight.again && flight.promise === promise);
    } finally {
      if (flight.promise === promise) flight.promise = null;
    }
  })();
  flight.promise = promise;
  return promise;
}

function refreshIfStale(): void {
  if (!sidebarListsAreFresh()) void loadSidebarConversations();
}

/**
 * Keeps the rail's lists for whatever page shows it (chat, agents, artifacts). They live in the store, so a rail that
 * mounts again shows them at once: it loads when nothing is loaded yet, and refreshes in the background when they are
 * older than {@link SIDEBAR_LISTS_STALE_MS}, when the window comes back into focus after that, and whenever something
 * bumps `conversationsVersion`. `enabled` false (an agent's or a project's chats show instead): nothing loads until it
 * is true again, and then only what is stale.
 */
export function useSidebarConversations(enabled = true): void {
  // Run dots come from each chat's `harnessState`, so while one listed chat is working the lists are read again on a
  // bounded clock (classic jobs plan §6); pushes and a job's end read them in between (`onJobListsRefresh` below).
  const working = useChatStore((s) => anyWorking([s.conversations, s.sharedConversations, s.agentConversations]));
  useJobListsRefresh(enabled && working);
  // Sign-out clears every rail list, also when this rail shows an agent's or a project's chats and never loads these.
  useEffect(() => watchSidebarSession(), []);
  useEffect(() => {
    if (!enabled) return undefined;
    refreshIfStale();
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshIfStale();
    };
    window.addEventListener('focus', refreshIfStale);
    document.addEventListener('visibilitychange', onVisible);
    const stopVersion = useChatStore.subscribe((state, prev) => {
      if (state.conversationsVersion !== prev.conversationsVersion) void loadSidebarConversations({ again: true });
    });
    // Projects turned on or off since Your chats was read changes which chats it lists.
    const stopProjects = useFeatureFlagsStore.subscribe((state) => {
      if (ownedReadWithProjects !== null && selectProjectsEnabled(state) !== ownedReadWithProjects) {
        void loadSidebarConversations({ again: true });
      }
    });
    // These lists are not SWR's: a job that moved (refreshJobLists) reads them again here.
    const stopJobs = onJobListsRefresh(() => void loadSidebarConversations({ again: true }));
    return () => {
      window.removeEventListener('focus', refreshIfStale);
      document.removeEventListener('visibilitychange', onVisible);
      stopVersion();
      stopProjects();
      stopJobs();
    };
  }, [enabled]);
}

let watchedUserId: string | null | undefined;

/**
 * Signing out does not reload the page on the desktop app, so the rails' lists go with the person who signed out:
 * the next person to sign in never sees their chats, not even while their own are loading. A load still out for the
 * last person is dropped too, so the next person's rail starts its own. Installed when a rail that keeps a list in the
 * store mounts, and by the first load, not as this module loads: modules that load with the app's config import it.
 */
export function watchSidebarSession(): void {
  if (watchedUserId !== undefined || typeof window === 'undefined') return;
  watchedUserId = getCurrentUserId();
  useAuthStore.subscribe(() => {
    const userId = getCurrentUserId();
    if (userId === watchedUserId) return;
    watchedUserId = userId;
    loadedVersion = -1;
    ownedReadWithProjects = null;
    inFlight = null;
    loadAgain = false;
    for (const flight of Object.values(listFlights)) {
      flight.promise = null;
      flight.again = false;
    }
    useChatStore.getState().clearSidebarLists();
  });
}

/** Tests only: forget the loads in flight and what was loaded. */
export function resetSidebarConversationsForTests(): void {
  inFlight = null;
  loadAgain = false;
  loadedVersion = -1;
  ownedReadWithProjects = null;
  for (const flight of Object.values(listFlights)) {
    flight.promise = null;
    flight.again = false;
  }
}
