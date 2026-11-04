import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

import { useChatStore } from '@/chat/store';
import type { Conversation } from '@/chat/types';
import { useAuthStore } from '@/lib/store/auth-store';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { clearSessionCache } from '@/lib/api/session-cache';
import { useSidebarConversations } from '../use-sidebar-conversations';

const fetchConversations = vi.fn();
vi.mock('@/chat/api', () => ({
  ChatApi: {
    fetchConversations: (...args: unknown[]) => fetchConversations(...args),
  },
}));

// SWR's default cache binds the real setTimeout for focus events when it loads.
const realSetTimeout = globalThis.setTimeout;

const PAGINATION = {
  page: 1,
  limit: 10,
  totalCount: 0,
  totalPages: 0,
  hasNextPage: false,
  hasPrevPage: false,
};

type Source = 'owned' | 'shared';
type Options = { source: Source; excludeProjectChats?: boolean };

function chat(id: string, title = id): Conversation {
  return { id, title, createdAt: '2026-01-01', updatedAt: '2026-01-01', isShared: false, sharedWith: [] };
}

function page(...rows: Conversation[]) {
  return { conversations: rows, source: 'owned', pagination: PAGINATION };
}

/** Answers owned with `owned`, shared with nothing. */
function answerOwned(...owned: Conversation[]) {
  fetchConversations.mockImplementation(async (_page: number, _limit: number, opts: Options) =>
    opts.source === 'owned' ? page(...owned) : page(),
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const ownedCalls = () =>
  fetchConversations.mock.calls.filter(([, , opts]) => (opts as Options).source === 'owned').length;
const ownedTitles = () => useChatStore.getState().conversations.map((c) => c.title);

/** Lets fetches settle and SWR's frame-delayed revalidation run. */
async function settle(ms = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function mount() {
  const view = renderHook(() => useSidebarConversations());
  await settle();
  return view;
}

async function focusWindow() {
  await act(async () => {
    window.dispatchEvent(new Event('focus'));
    await new Promise((r) => realSetTimeout(r, 10));
  });
  await settle();
}

describe('useSidebarConversations', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    clearSessionCache();
    useServicesHealthStore.setState({ apiServerReachable: true });
    useFeatureFlagsStore.setState({ flags: {} } as never);
    answerOwned(chat('a', 'A'));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('loads owned and shared recents when the sidebar mounts, without error toasts', async () => {
    await mount();

    expect(fetchConversations).toHaveBeenCalledTimes(2);
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), {
      source: 'owned',
      excludeProjectChats: false,
      suppressErrorToast: true,
    });
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), {
      source: 'shared',
      suppressErrorToast: true,
    });
    expect(ownedTitles()).toEqual(['A']);
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
  });

  it('leaves project chats out of Recents while Projects is on, since they list under their project', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never);
    await mount();

    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), {
      source: 'owned',
      excludeProjectChats: true,
      suppressErrorToast: true,
    });
  });

  it('waits for the feature flags, then fetches once with the right filter', async () => {
    useFeatureFlagsStore.setState({ flags: null } as never);
    await mount();
    expect(fetchConversations).not.toHaveBeenCalled();
    expect(useChatStore.getState().isConversationsLoading).toBe(true);

    act(() => useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never));
    await settle();

    expect(fetchConversations).toHaveBeenCalledTimes(2);
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), expect.objectContaining({
      source: 'owned',
      excludeProjectChats: true,
    }));
  });

  it('fetches once on mount even after the lists were refreshed earlier in the session', async () => {
    useChatStore.setState({ conversationsVersion: 3 });
    await mount();

    expect(fetchConversations).toHaveBeenCalledTimes(2);
  });

  it('reuses the lists for a remount within 30 s', async () => {
    const first = await mount();
    first.unmount();
    await settle(29_000);
    await mount();

    expect(fetchConversations).toHaveBeenCalledTimes(2);
    expect(ownedTitles()).toEqual(['A']);
  });

  it('refreshes in the background after 30 s, keeping the lists on screen meanwhile', async () => {
    const first = await mount();
    first.unmount();
    await settle(31_000);

    const owned = deferred<ReturnType<typeof page>>();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: Options) =>
      opts.source === 'owned' ? owned.promise : Promise.resolve(page()),
    );
    await mount();

    expect(ownedCalls()).toBe(2);
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
    expect(ownedTitles()).toEqual(['A']);

    await act(async () => owned.resolve(page(chat('b', 'B'), chat('a', 'A'))));
    await settle();
    expect(ownedTitles()).toEqual(['B', 'A']);
  });

  it('keeps edits made since the last fetch when it remounts within the window', async () => {
    const first = await mount();
    act(() => useChatStore.getState().renameConversation('a', 'Renamed'));
    first.unmount();
    await settle(5_000);
    await mount();

    expect(fetchConversations).toHaveBeenCalledTimes(2);
    expect(ownedTitles()).toEqual(['Renamed']);
  });

  it('refetches on tab focus once the lists are older than 30 s, not before', async () => {
    await mount();
    await settle(10_000);
    await focusWindow();
    expect(fetchConversations).toHaveBeenCalledTimes(2);

    await settle(21_000);
    answerOwned(chat('b', 'B'));
    await focusWindow();
    await vi.waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(4));
    await settle();
    expect(ownedTitles()).toEqual(['B']);
  });

  it('refetches once when the lists change while the sidebar is shown', async () => {
    await mount();
    answerOwned(chat('b', 'B'), chat('a', 'A'));

    act(() => useChatStore.getState().bumpConversationsVersion());
    await settle();

    expect(fetchConversations).toHaveBeenCalledTimes(4);
    expect(ownedTitles()).toEqual(['B', 'A']);
  });

  it('refetches on the next mount when the lists changed while the sidebar was away', async () => {
    const first = await mount();
    first.unmount();
    useChatStore.getState().bumpConversationsVersion();
    await settle(1_000);
    await mount();

    expect(fetchConversations).toHaveBeenCalledTimes(4);
  });

  it('keeps the newest answer when an older request lands after it', async () => {
    const older = deferred<ReturnType<typeof page>>();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: Options) =>
      opts.source === 'owned' ? older.promise : Promise.resolve(page()),
    );
    await mount();

    answerOwned(chat('new', 'New'));
    act(() => useChatStore.getState().bumpConversationsVersion());
    await settle();
    await act(async () => older.resolve(page(chat('old', 'Old'))));
    await settle();

    expect(ownedTitles()).toEqual(['New']);
  });

  it('shows the list for the current Projects setting when it is switched back within the window', async () => {
    fetchConversations.mockImplementation(async (_p: number, _l: number, opts: Options) =>
      opts.source !== 'owned' ? page() : page(chat(opts.excludeProjectChats ? 'recents' : 'with-projects')),
    );
    await mount();
    expect(ownedTitles()).toEqual(['with-projects']);

    act(() => useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never));
    await settle();
    expect(ownedTitles()).toEqual(['recents']);

    act(() => useFeatureFlagsStore.setState({ flags: {} } as never));
    await settle();

    expect(ownedTitles()).toEqual(['with-projects']);
    expect(fetchConversations).toHaveBeenCalledTimes(4);
  });

  it('marks the list as failed, without retrying on its own', async () => {
    fetchConversations.mockRejectedValue(new Error('Request failed with status code 500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await mount();

    expect(useChatStore.getState().conversationsError).toBeTruthy();
    expect(useChatStore.getState().isConversationsLoading).toBe(false);

    await settle(60_000);
    expect(fetchConversations).toHaveBeenCalledTimes(2);
  });

  it('asks again at most once per focus while the server keeps failing', async () => {
    fetchConversations.mockRejectedValue(new Error('500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await mount();
    await settle(6_000);

    await focusWindow();
    await vi.waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(4));
    await focusWindow();
    expect(fetchConversations).toHaveBeenCalledTimes(4);
  });

  it('keeps showing the list, not "Failed to load", when a background refresh fails', async () => {
    const first = await mount();
    first.unmount();
    await settle(31_000);
    fetchConversations.mockRejectedValue(new Error('500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await mount();

    expect(fetchConversations).toHaveBeenCalledTimes(4);
    expect(ownedTitles()).toEqual(['A']);
    expect(useChatStore.getState().conversationsError).toBeNull();
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
  });

  it('shows the skeleton, not an earlier failure, while the next request runs', async () => {
    fetchConversations.mockRejectedValue(new Error('500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const first = await mount();
    expect(useChatStore.getState().conversationsError).toBeTruthy();
    first.unmount();

    const owned = deferred<ReturnType<typeof page>>();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: Options) =>
      opts.source === 'owned' ? owned.promise : Promise.resolve(page()),
    );
    await mount();

    expect(useChatStore.getState().conversationsError).toBeNull();
    expect(useChatStore.getState().isConversationsLoading).toBe(true);

    await act(async () => owned.resolve(page(chat('a', 'A'))));
    await settle();
    expect(ownedTitles()).toEqual(['A']);
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
  });

  it('shows no failure while the API server is unreachable', async () => {
    useServicesHealthStore.setState({ apiServerReachable: false });
    fetchConversations.mockRejectedValue(new Error('Network Error'));
    await mount();

    expect(useChatStore.getState().conversationsError).toBeNull();
  });

  it('clears the failure once a later fetch succeeds', async () => {
    fetchConversations.mockRejectedValueOnce(new Error('500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const first = await mount();
    expect(useChatStore.getState().conversationsError).toBeTruthy();
    first.unmount();

    await mount();
    expect(useChatStore.getState().conversationsError).toBeNull();
    expect(ownedTitles()).toEqual(['A']);
  });

  it('forgets the lists at sign-out and fetches again for the next session', async () => {
    const first = await mount();
    first.unmount();
    act(() => useAuthStore.getState().logout());

    expect(useChatStore.getState().conversations).toEqual([]);
    // The last session's flags are forgotten too; the next one waits for its own.
    expect(useFeatureFlagsStore.getState().flags).toBeNull();
    useFeatureFlagsStore.setState({ flags: {} } as never);
    await mount();
    expect(fetchConversations).toHaveBeenCalledTimes(4);
  });

  it('still refreshes after a write once the org was switched on this screen', async () => {
    await mount();
    answerOwned(chat('b', 'B'));
    act(() => clearSessionCache({ refetch: true }));
    await settle();
    expect(ownedTitles()).toEqual(['B']);

    answerOwned(chat('c', 'C'), chat('b', 'B'));
    act(() => useChatStore.getState().bumpConversationsVersion());
    await settle();

    expect(fetchConversations).toHaveBeenCalledTimes(6);
    expect(ownedTitles()).toEqual(['C', 'B']);
  });

  it('does not write a response that was in flight at sign-out', async () => {
    const owned = deferred<ReturnType<typeof page>>();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: Options) =>
      opts.source === 'owned' ? owned.promise : Promise.resolve(page()),
    );
    const view = renderHook(() => useSidebarConversations());
    await settle();

    act(() => useAuthStore.getState().logout());
    view.unmount();
    await act(async () => owned.resolve(page(chat('a', 'From the last user'))));
    await settle();

    expect(useChatStore.getState().conversations).toEqual([]);
  });
});
