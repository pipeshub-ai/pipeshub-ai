'use client';

import { useEffect } from 'react';
import { mutate } from 'swr';
import { SWR_NS } from '@/lib/api/swr-cache';
import type { ConversationRunState } from '@/lib/contracts/harness/generated/ext/pipeshub/api/conversation-run-state';

/**
 * The cached reads the background-job screens share (classic jobs plan §6), and the one way to refresh them. The
 * Needs you count and list are not here: a socket push is their source of truth (`inbox-store.ts`).
 */

export const HARNESS_JOBS_NS = 'harness-jobs';

/** Rows one read of the person's jobs asks for (the route's maximum). */
export const PERSON_JOBS_PAGE = 100;

export type PersonJobsKey = readonly [typeof HARNESS_JOBS_NS, 'person', { limit: number }];

/** The Jobs page's first page of the caller's own jobs across their chats. */
export const PERSON_JOBS_KEY: PersonJobsKey = [HARNESS_JOBS_NS, 'person', { limit: PERSON_JOBS_PAGE }];

/** How often the sidebar re-reads its chats while one of them is working (and only then). */
export const WORKING_REFRESH_MS = 30_000;

const isPersonJobsKey = (key: unknown): boolean => Array.isArray(key) && key[0] === HARNESS_JOBS_NS;
/** The sidebar's own key (`use-sidebar-conversations.ts`), whatever its page size and project filter. */
const isSidebarKey = (key: unknown): boolean => Array.isArray(key) && key[0] === SWR_NS.conversationLists && key[1] === 'sidebar';

const listeners = new Set<() => void>();

/** For a list kept outside SWR (the agent sidebar): called on every refresh below. Returns the unsubscribe. */
export function onJobListsRefresh(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Re-reads the sidebar's chats (their `harnessState` dots) and the person's jobs, wherever they are mounted. Called
 * when something says a job moved: an `inboxCount` push changed `waiting`, a followed job ended, the open chat's
 * run changed state, or the tab came back.
 */
export function refreshJobLists(): void {
  void mutate(isSidebarKey);
  void mutate(isPersonJobsKey);
  listeners.forEach((listener) => listener());
}

/** Whether any listed chat is working now (anyone's run), the only time the sidebar re-reads on a clock. */
export function anyWorking(lists: ReadonlyArray<ReadonlyArray<{ harnessState?: ConversationRunState | null }>>): boolean {
  return lists.some((list) => list.some((c) => c.harnessState?.runState === 'working' || Boolean(c.harnessState?.activeRun)));
}

/**
 * The sidebar's bounded freshness: one re-read every 30 s while some chat is working, none at 0 working (that
 * covers runs started on another device), and one when the tab becomes visible again.
 */
export function useJobListsRefresh(working: boolean): void {
  useEffect(() => {
    if (!working) return undefined;
    const timer = window.setInterval(refreshJobLists, WORKING_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [working]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshJobLists();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);
}
