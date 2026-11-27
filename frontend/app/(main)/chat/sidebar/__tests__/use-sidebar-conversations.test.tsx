import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';

import { useChatStore } from '@/chat/store';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useAuthStore } from '@/config';
import { SIDEBAR_LISTS_STALE_MS } from '../../constants';
import {
  loadSidebarConversations,
  refreshSidebarList,
  resetSidebarConversationsForTests,
  useSidebarConversations,
} from '../use-sidebar-conversations';

const fetchConversations = vi.fn();
vi.mock('@/chat/api', () => ({
  ChatApi: {
    fetchConversations: (...args: unknown[]) => fetchConversations(...args),
  },
}));

const PAGINATION = {
  page: 1,
  limit: 10,
  totalCount: 0,
  totalPages: 0,
  hasNextPage: false,
  hasPrevPage: false,
};

const row = (id: string) =>
  ({ id, title: `Chat ${id}`, createdAt: '', updatedAt: '', isShared: false, sharedWith: [], isOwner: true }) as never;

/** A response the test answers when it wants to, so it can look at the rail while the request is out. */
function held() {
  let answer: (value: unknown) => void = () => {};
  const promise = new Promise((resolve) => {
    answer = resolve;
  });
  return { promise, answer: (conversations: unknown[]) => answer({ conversations, pagination: PAGINATION }) };
}

/** Lists that loaded `ageMs` ago, as a rail left them. */
function listsLoaded(ids: string[], ageMs: number) {
  useChatStore.setState({ conversations: ids.map(row), conversationsLoadedAt: Date.now() - ageMs });
}

describe('useSidebarConversations', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetSidebarConversationsForTests();
    useChatStore.getState().reset();
    useServicesHealthStore.setState({ apiServerReachable: true });
    // The flags are read (Projects off), as they are once the app has started.
    useFeatureFlagsStore.setState({ flags: {} } as never);
    fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
  });

  afterEach(() => cleanup());

  it('loads owned and shared recents when the sidebar mounts', async () => {
    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(2));
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), {
      source: 'owned',
      excludeProjectChats: false,
    });
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), { source: 'shared' });
  });

  it('leaves project chats out of Recents while Projects is on, since they list under their project', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never);
    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(2));
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), {
      source: 'owned',
      excludeProjectChats: true,
    });
  });

  it('marks the conversation list as failed when it cannot be loaded', async () => {
    fetchConversations.mockRejectedValue(new Error('Request failed with status code 500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(useChatStore.getState().conversationsError).toBeTruthy());
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
  });

  it('shows the skeleton for the first load only, and stamps when the lists arrived', async () => {
    const owned = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? owned.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );
    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(useChatStore.getState().isConversationsLoading).toBe(true));
    await act(async () => owned.answer([row('a')]));

    expect(useChatStore.getState().isConversationsLoading).toBe(false);
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['a']);
    expect(useChatStore.getState().conversationsLoadedAt).not.toBeNull();
  });

  it('a rail mounted again within 30 s shows its lists as they are and asks for nothing', async () => {
    const first = renderHook(() => useSidebarConversations());
    await waitFor(() => expect(useChatStore.getState().conversationsLoadedAt).not.toBeNull());
    first.unmount();

    renderHook(() => useSidebarConversations());

    expect(fetchConversations).toHaveBeenCalledTimes(2);
  });

  it('a rail mounted with stale lists refreshes them in the background: rows stay, no skeleton, no error toast', async () => {
    listsLoaded(['a', 'b'], SIDEBAR_LISTS_STALE_MS + 1);
    const owned = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? owned.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );

    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(2));
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), {
      source: 'owned',
      excludeProjectChats: false,
      suppressErrorToast: true,
    });
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['a', 'b']);

    await act(async () => owned.answer([row('c'), row('a')]));
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['c', 'a']);
  });

  it('one load on mount, even when earlier changes bumped the version', async () => {
    useChatStore.setState({ conversationsVersion: 3 });

    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(useChatStore.getState().conversationsLoadedAt).not.toBeNull());
    expect(fetchConversations).toHaveBeenCalledTimes(2);
  });

  it('a bump while mounted refreshes once; bumps during that load run it once more, not once each', async () => {
    renderHook(() => useSidebarConversations());
    await waitFor(() => expect(useChatStore.getState().conversationsLoadedAt).not.toBeNull());
    fetchConversations.mockClear();
    const owned = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? owned.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );

    act(() => useChatStore.getState().bumpConversationsVersion());
    act(() => useChatStore.getState().bumpConversationsVersion());
    act(() => useChatStore.getState().bumpConversationsVersion());
    expect(fetchConversations).toHaveBeenCalledTimes(2);

    fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
    await act(async () => owned.answer([]));
    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(4));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(fetchConversations).toHaveBeenCalledTimes(4);
  });

  it('a refresh that fails keeps the rows and says nothing', async () => {
    listsLoaded(['a'], SIDEBAR_LISTS_STALE_MS + 1);
    fetchConversations.mockRejectedValue(new Error('Request failed with status code 500'));
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(2));
    await act(async () => {
      await loadSidebarConversations();
    });
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['a']);
    expect(useChatStore.getState().conversationsError).toBeNull();
    expect(errors).not.toHaveBeenCalled();
  });

  it('the window coming back into focus refreshes stale lists only', async () => {
    renderHook(() => useSidebarConversations());
    await waitFor(() => expect(useChatStore.getState().conversationsLoadedAt).not.toBeNull());
    fetchConversations.mockClear();

    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    expect(fetchConversations).not.toHaveBeenCalled();

    useChatStore.setState({ conversationsLoadedAt: Date.now() - SIDEBAR_LISTS_STALE_MS - 1 });
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(2));
  });

  it('a bump while no rail is mounted makes the next mount load, even within 30 s', async () => {
    const first = renderHook(() => useSidebarConversations());
    await waitFor(() => expect(useChatStore.getState().conversationsLoadedAt).not.toBeNull());
    first.unmount();
    act(() => useChatStore.getState().bumpConversationsVersion());
    expect(fetchConversations).toHaveBeenCalledTimes(2);

    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(4));
  });
});

describe('a reload: Your chats is read once, after the flags', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetSidebarConversationsForTests();
    useChatStore.getState().reset();
    useServicesHealthStore.setState({ apiServerReachable: true });
    useFeatureFlagsStore.setState({ flags: null } as never);
    fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
  });

  it('reads Shared chats at once and Your chats once the flags are known, with Projects as they say', async () => {
    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(1));
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), { source: 'shared' });

    act(() => useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never));

    await waitFor(() => expect(useChatStore.getState().conversationsLoadedAt).not.toBeNull());
    expect(fetchConversations).toHaveBeenCalledTimes(2);
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), { source: 'owned', excludeProjectChats: true });
  });

  it('Projects turned on later reads Your chats again', async () => {
    useFeatureFlagsStore.setState({ flags: {} } as never);
    renderHook(() => useSidebarConversations());
    await waitFor(() => expect(useChatStore.getState().conversationsLoadedAt).not.toBeNull());
    fetchConversations.mockClear();

    act(() => useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never));

    await waitFor(() =>
      expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), {
        source: 'owned',
        excludeProjectChats: true,
        suppressErrorToast: true,
      }),
    );
  });
});

/** A list the server pages as it does: pins first, then newest activity; `limit` rows a page. */
function pagedList(pinned: number, others: number) {
  const rows = [
    ...Array.from({ length: pinned }, (_, i) => ({ ...(row(`p${i + 1}`) as object), isPinned: true })),
    ...Array.from({ length: others }, (_, i) => row(`r${i + 1}`) as object),
  ];
  return (page: number, limit: number) =>
    Promise.resolve({
      conversations: rows.slice((page - 1) * limit, page * limit),
      pagination: { ...PAGINATION, page, limit, hasNextPage: page * limit < rows.length },
    });
}

describe('every pin of a list reaches the rail, with five unpinned chats under them', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetSidebarConversationsForTests();
    useChatStore.getState().reset();
    useServicesHealthStore.setState({ apiServerReachable: true });
    useFeatureFlagsStore.setState({ flags: {} } as never);
  });

  const pagesRead = (source: string) =>
    fetchConversations.mock.calls.filter(([, , opts]) => (opts as { source: string }).source === source).map(([n]) => n);
  const ids = (list: Array<{ id: string; isPinned?: boolean }>) => list.map((c) => `${c.id}${c.isPinned ? '*' : ''}`);

  it('12 pinned over a page of 10: reads on until five unpinned are in, so all 12 pins and the others reach the rail', async () => {
    const shared = pagedList(12, 8);
    fetchConversations.mockImplementation((n: number, limit: number, opts: { source: string }) =>
      opts.source === 'shared' ? shared(n, limit) : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );

    await loadSidebarConversations();

    expect(pagesRead('shared')).toEqual([1, 2]);
    expect(pagesRead('owned')).toEqual([1]);
    const { sharedConversations, sharedPagination } = useChatStore.getState();
    expect(ids(sharedConversations).filter((id) => id.endsWith('*'))).toHaveLength(12);
    expect(ids(sharedConversations).slice(12)).toEqual(['r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8']);
    expect(sharedPagination?.hasNextPage).toBe(false);
  });

  it('a first page with five unpinned chats is read alone, its next page left to More', async () => {
    const owned = pagedList(5, 20);
    fetchConversations.mockImplementation((n: number, limit: number, opts: { source: string }) =>
      opts.source === 'owned' ? owned(n, limit) : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );

    await loadSidebarConversations();

    expect(pagesRead('owned')).toEqual([1]);
    expect(useChatStore.getState().conversations).toHaveLength(10);
    expect(useChatStore.getState().pagination?.hasNextPage).toBe(true);
  });

  it('a list that is all pins reads to its end', async () => {
    const owned = pagedList(23, 0);
    fetchConversations.mockImplementation((n: number, limit: number, opts: { source: string }) =>
      opts.source === 'owned' ? owned(n, limit) : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );

    await loadSidebarConversations();

    expect(pagesRead('owned')).toEqual([1, 2, 3]);
    expect(useChatStore.getState().conversations).toHaveLength(23);
  });

  it('a refresh of one list reads on the same way', async () => {
    useChatStore.setState({ conversationsLoadedAt: Date.now() });
    const shared = pagedList(12, 8);
    fetchConversations.mockImplementation((n: number, limit: number) => shared(n, limit));

    await refreshSidebarList('shared');

    expect(pagesRead('shared')).toEqual([1, 2]);
    expect(useChatStore.getState().sharedConversations).toHaveLength(20);
  });

  it('a chat that moves up between two pages is listed once', async () => {
    // r1 had a new message between the two reads, so page 2 brings it again, on top.
    const first = pagedList(9, 6);
    fetchConversations.mockImplementation((n: number, limit: number, opts: { source: string }) => {
      if (opts.source !== 'owned') return Promise.resolve({ conversations: [], pagination: PAGINATION });
      return n === 1 ? first(1, limit) : first(2, limit).then((r) => ({ ...r, conversations: [row('r1'), ...r.conversations] }));
    });

    await loadSidebarConversations();

    const listed = ids(useChatStore.getState().conversations);
    expect(new Set(listed).size).toBe(listed.length);
    expect(listed.filter((id) => id === 'r1')).toHaveLength(1);
  });
});

describe('refreshSidebarList: one list, when only it can have changed', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    resetSidebarConversationsForTests();
    useChatStore.getState().reset();
    useServicesHealthStore.setState({ apiServerReachable: true });
    fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
  });

  it('reads Shared chats alone, and a chat that moved there leaves Your chats', async () => {
    listsLoaded(['a', 'b'], 0);
    fetchConversations.mockResolvedValue({ conversations: [row('b'), row('s')], pagination: PAGINATION });

    await act(async () => {
      await refreshSidebarList('shared');
    });

    expect(fetchConversations).toHaveBeenCalledTimes(1);
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), { source: 'shared', suppressErrorToast: true });
    expect(useChatStore.getState().sharedConversations.map((c) => c.id)).toEqual(['b', 's']);
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['a']);
  });

  it('asks nothing before the rail has loaded at all', async () => {
    await refreshSidebarList('shared');
    expect(fetchConversations).not.toHaveBeenCalled();
  });

  it('a failed read keeps the rows', async () => {
    listsLoaded(['a'], 0);
    useChatStore.setState({ sharedConversations: [row('s')] });
    fetchConversations.mockRejectedValue(new Error('500'));

    await refreshSidebarList('shared');

    expect(useChatStore.getState().sharedConversations.map((c) => c.id)).toEqual(['s']);
  });
});

describe('signing out', () => {
  const b64 = (value: unknown) => btoa(JSON.stringify(value)).replace(/=+$/, '');
  const tokenFor = (userId: string) => `${b64({ alg: 'HS256' })}.${b64({ userId })}.sig`;

  beforeEach(async () => {
    vi.clearAllMocks();
    resetSidebarConversationsForTests();
    useChatStore.getState().reset();
    useServicesHealthStore.setState({ apiServerReachable: true });
    useFeatureFlagsStore.setState({ flags: {} } as never);
    useAuthStore.setState({ accessToken: tokenFor('user-a'), isAuthenticated: true } as never);
    fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
    // The watch starts with the first load, as it does when a rail first loads its lists.
    await loadSidebarConversations();
    vi.clearAllMocks();
    fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
  });

  afterEach(() => {
    cleanup();
    useAuthStore.setState({ accessToken: null, isAuthenticated: false } as never);
  });

  it('clears every rail list, so the next person never sees these titles', () => {
    useChatStore.setState({
      conversations: [row('a')],
      sharedConversations: [row('s')],
      conversationsLoadedAt: Date.now(),
      agentConversations: [row('g')],
      agentConversationsAgentId: 'agent-1',
      isMoreChatsPanelOpen: true,
      moreChatsSectionType: 'your',
    });

    act(() => useAuthStore.setState({ accessToken: null, isAuthenticated: false } as never));

    const state = useChatStore.getState();
    expect(state.conversations).toEqual([]);
    expect(state.sharedConversations).toEqual([]);
    expect(state.conversationsLoadedAt).toBeNull();
    expect(state.agentConversations).toEqual([]);
    expect(state.agentConversationsAgentId).toBeNull();
    expect(state.isMoreChatsPanelOpen).toBe(false);
  });

  it('a token refresh for the same person keeps the lists', () => {
    useChatStore.setState({ conversations: [row('a')], conversationsLoadedAt: Date.now() });

    act(() => useAuthStore.setState({ accessToken: tokenFor('user-a') } as never));

    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['a']);
  });

  it("the next person's rail loads their own lists, even while the last person's load is still out", async () => {
    let answerA: (value: unknown) => void = () => {};
    const heldA = new Promise((resolve) => (answerA = resolve));
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? heldA : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );
    // The last person's rail has nothing yet: its first load is the one still out.
    useChatStore.setState({ conversationsLoadedAt: null });
    const railA = renderHook(() => useSidebarConversations());
    expect(fetchConversations).toHaveBeenCalledTimes(2);
    railA.unmount();

    // Signed out and someone else signed in, with no page load between (the desktop app).
    act(() => useAuthStore.setState({ accessToken: null, isAuthenticated: false } as never));
    act(() => useAuthStore.setState({ accessToken: tokenFor('user-b'), isAuthenticated: true } as never));
    fetchConversations.mockImplementation(async (_p: number, _l: number, opts: { source: string }) => ({
      conversations: opts.source === 'owned' ? [row('b1')] : [],
      pagination: PAGINATION,
    }));
    renderHook(() => useSidebarConversations());
    await act(async () => {
      answerA({ conversations: [row('a1')], pagination: PAGINATION });
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const state = useChatStore.getState();
    expect(fetchConversations).toHaveBeenCalledTimes(4);
    expect(state.conversations.map((c) => c.id)).toEqual(['b1']);
    expect(state.isConversationsLoading).toBe(false);
  });

  it("a load dropped at the change of person never runs again in place of the next person's own", async () => {
    const ownedA = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? ownedA.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );
    void loadSidebarConversations();
    act(() => useAuthStore.setState({ accessToken: tokenFor('user-b'), isAuthenticated: true } as never));
    const ownedB = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? ownedB.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );
    void loadSidebarConversations();
    // A chat changed while the next person's load was out: their lists load once more after it.
    void loadSidebarConversations({ again: true });
    fetchConversations.mockImplementation(async (_p: number, _l: number, opts: { source: string }) => ({
      conversations: opts.source === 'owned' ? [row('b-new')] : [],
      pagination: PAGINATION,
    }));

    await act(async () => {
      ownedA.answer([row('a1')]);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    await act(async () => ownedB.answer([row('b-old')]));
    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(6));

    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['b-new']);
  });

  it("a one-list refresh still out at the change of person is dropped: the next person's starts at once", async () => {
    await loadSidebarConversations();
    const sharedA = held();
    fetchConversations.mockImplementation(() => sharedA.promise);
    void refreshSidebarList('shared');
    expect(fetchConversations).toHaveBeenCalledTimes(3);

    act(() => useAuthStore.setState({ accessToken: tokenFor('user-b'), isAuthenticated: true } as never));
    fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
    await loadSidebarConversations();
    const sharedB = held();
    fetchConversations.mockImplementation(() => sharedB.promise);
    void refreshSidebarList('shared');
    expect(fetchConversations).toHaveBeenCalledTimes(6);

    // The last person's answer lands: dropped, and the next person's refresh is still the one out.
    await act(async () => sharedA.answer([row('a-shared')]));
    void refreshSidebarList('shared');
    expect(fetchConversations).toHaveBeenCalledTimes(6);
    fetchConversations.mockResolvedValue({ conversations: [row('b-shared')], pagination: PAGINATION });
    await act(async () => sharedB.answer([]));
    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(7));
    await waitFor(() => expect(useChatStore.getState().sharedConversations.map((c) => c.id)).toEqual(['b-shared']));
  });

  it("the last person's first load landing late leaves the next person's skeleton on", async () => {
    const ownedA = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? ownedA.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );
    // The last person's rail has nothing yet: its first load is the one still out.
    useChatStore.setState({ conversationsLoadedAt: null });
    void loadSidebarConversations();
    expect(useChatStore.getState().isConversationsLoading).toBe(true);

    act(() => useAuthStore.setState({ accessToken: null, isAuthenticated: false } as never));
    act(() => useAuthStore.setState({ accessToken: tokenFor('user-b'), isAuthenticated: true } as never));
    const ownedB = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? ownedB.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );
    void loadSidebarConversations();
    await act(async () => {
      ownedA.answer([row('a1')]);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(useChatStore.getState().conversations).toEqual([]);
    expect(useChatStore.getState().isConversationsLoading).toBe(true);

    await act(async () => {
      ownedB.answer([row('b1')]);
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['b1']);
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
  });

  it('a sign-out with nobody signing in leaves no skeleton on when the last load lands', async () => {
    const ownedA = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? ownedA.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );
    useChatStore.setState({ conversationsLoadedAt: null });
    const loading = loadSidebarConversations();

    act(() => useAuthStore.setState({ accessToken: null, isAuthenticated: false } as never));
    await act(async () => {
      ownedA.answer([row('a1')]);
      await loading;
    });

    expect(useChatStore.getState().isConversationsLoading).toBe(false);
    expect(useChatStore.getState().conversations).toEqual([]);
  });

  it('drops a load that lands after the person signed out', async () => {
    const owned = held();
    fetchConversations.mockImplementation((_p: number, _l: number, opts: { source: string }) =>
      opts.source === 'owned' ? owned.promise : Promise.resolve({ conversations: [], pagination: PAGINATION }),
    );
    const loading = loadSidebarConversations();

    act(() => useAuthStore.setState({ accessToken: tokenFor('user-b') } as never));
    await act(async () => {
      owned.answer([row('a')]);
      await loading;
    });

    expect(useChatStore.getState().conversations).toEqual([]);
    expect(useChatStore.getState().conversationsLoadedAt).toBeNull();
  });
});
