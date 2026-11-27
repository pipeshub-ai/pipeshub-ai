import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * CJ-1 (classic jobs plan §6, D7, D10): the shared cache keys and their one refresh, the sidebar's bounded clock, the
 * Needs you count and list fed by pushes, and the sidebar mark's priority over every `harnessState`.
 */

const mutate = vi.hoisted(() => vi.fn(async () => undefined));
vi.mock('swr', async (load) => ({ ...(await load<typeof import('swr')>()), mutate }));
const feed = vi.hoisted(() => vi.fn());
const count = vi.hoisted(() => vi.fn());
vi.mock('../inbox-api', () => ({ InboxApi: { feed: (o: unknown) => feed(o), count: () => count() }, personTimeZone: () => 'UTC' }));

const { PERSON_JOBS_KEY, WORKING_REFRESH_MS, anyWorking, onJobListsRefresh, refreshJobLists, useJobListsRefresh } = await import('../swr-keys');
const { EMPTY_COUNTS, openNeedsYou, takeCounts, useInboxStore } = await import('../inbox-store');
const { chatDot, sidebarDot } = await import('../jobs-list');

const counts = (waiting: number) => ({ ...EMPTY_COUNTS, waiting, badge: waiting });
const matched = (key: unknown) => mutate.mock.calls.some(([matcher]) => typeof matcher === 'function' && (matcher as (k: unknown) => boolean)(key));

beforeEach(() => {
  mutate.mockClear();
  feed.mockReset();
  count.mockReset();
  useInboxStore.setState({ open: false, counts: EMPTY_COUNTS, countsKnown: false, items: [], filter: 'all', view: 'inbox', seq: 0 });
});

afterEach(() => vi.useRealTimers());

describe('the job lists’ keys and their refresh', () => {
  it('names the person’s jobs once, and one refresh re-reads the sidebar’s chats, those jobs and lists outside SWR', () => {
    expect(PERSON_JOBS_KEY).toEqual(['harness-jobs', 'person', { limit: 100 }]);
    const outside = vi.fn();
    const off = onJobListsRefresh(outside);
    refreshJobLists();
    expect(matched(['conversations/lists', 'sidebar', { limit: 30, excludeProjectChats: false }])).toBe(true);
    expect(matched(PERSON_JOBS_KEY)).toBe(true);
    expect(matched(['conversations/lists', 'more', {}])).toBe(false);
    expect(matched(['agents/lists'])).toBe(false);
    expect(outside).toHaveBeenCalledTimes(1);
    off();
    refreshJobLists();
    expect(outside).toHaveBeenCalledTimes(1);
  });

  it('reads on a 30 s clock only while some chat works, stops at none, and once when the tab comes back', () => {
    vi.useFakeTimers();
    const { rerender, unmount } = renderHook(({ working }) => useJobListsRefresh(working), { initialProps: { working: false } });
    vi.advanceTimersByTime(WORKING_REFRESH_MS * 3);
    expect(mutate).not.toHaveBeenCalled();

    rerender({ working: true });
    vi.advanceTimersByTime(WORKING_REFRESH_MS);
    expect(mutate).toHaveBeenCalledTimes(2);

    rerender({ working: false });
    vi.advanceTimersByTime(WORKING_REFRESH_MS * 3);
    expect(mutate).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(mutate).toHaveBeenCalledTimes(4);
    unmount();
    document.dispatchEvent(new Event('visibilitychange'));
    expect(mutate).toHaveBeenCalledTimes(4);
  });

  it('counts a chat as working by its run state or its active run, in any list', () => {
    const quiet = { harnessState: { activeRun: null, runState: null, needsYou: false, finishedUnseen: true, seenUpTo: 1 } };
    expect(anyWorking([[quiet], [{}]])).toBe(false);
    expect(anyWorking([[quiet], [{ harnessState: { ...quiet.harnessState, runState: 'working' as const } }]])).toBe(true);
    expect(anyWorking([[{ harnessState: { ...quiet.harnessState, activeRun: { runId: 'r', status: 'running' as const } } }]])).toBe(true);
  });
});

describe('the Needs you count and list (D7)', () => {
  it('takes the first count quietly, then re-reads the lists only when waiting changes', () => {
    takeCounts(counts(2));
    expect(useInboxStore.getState().counts.waiting).toBe(2);
    expect(mutate).not.toHaveBeenCalled();

    takeCounts({ ...counts(2), unread: 5 });
    expect(mutate).not.toHaveBeenCalled();

    takeCounts(counts(1));
    expect(mutate).toHaveBeenCalledTimes(2);
  });

  it('reads the asks only (filter=waiting) when the page opens, and again on a push that changes the count', async () => {
    feed.mockResolvedValue({ items: [], counts: counts(1) });
    openNeedsYou();
    await vi.waitFor(() => expect(useInboxStore.getState().loading).toBe(false));
    expect(feed).toHaveBeenLastCalledWith({ filter: 'waiting', view: 'inbox' });

    takeCounts(counts(1));
    takeCounts(counts(0));
    await vi.waitFor(() => expect(feed).toHaveBeenCalledTimes(2));
    expect(feed).toHaveBeenLastCalledWith({ filter: 'waiting', view: 'inbox' });
  });

  it('gives the badge the server’s waiting count, live', async () => {
    const { useNeedsYouCount } = await import('../inbox-store');
    const { result } = renderHook(() => useNeedsYouCount());
    expect(result.current).toBe(0);
    act(() => takeCounts(counts(3)));
    expect(result.current).toBe(3);
  });
});

describe('a sidebar row’s one mark (D10)', () => {
  const state = (over: Record<string, unknown> = {}) =>
    ({ activeRun: null, runState: null, needsYou: false, finishedUnseen: false, seenUpTo: null, ...over }) as never;
  const local = (over: Partial<{ streaming: boolean; unread: boolean; active: boolean }> = {}) => ({ streaming: false, unread: false, active: false, ...over });

  it('puts needs you over working over finished, for every combination of the server’s flags', () => {
    for (const needsYou of [false, true])
      for (const runState of [null, 'working', 'needs_you'])
        for (const active of [false, true])
          for (const finishedUnseen of [false, true]) {
            const s = state({ needsYou, runState, finishedUnseen, activeRun: active ? { runId: 'r', status: 'running' } : null });
            const want = needsYou || runState === 'needs_you' ? 'needsYou' : runState === 'working' || active ? 'working' : finishedUnseen ? 'finished' : null;
            expect(chatDot(s)).toBe(want);
            expect(sidebarDot(s, local())?.dot ?? null).toBe(want);
          }
  });

  it('merges what this tab knows: its own stream is working, its own new answer is a finished mark', () => {
    expect(sidebarDot(undefined, local({ streaming: true }))).toEqual({ dot: 'working' });
    expect(sidebarDot(undefined, local({ unread: true }))).toEqual({ dot: 'finished' });
    expect(sidebarDot(state({ finishedUnseen: true }), local({ unread: true }))).toEqual({ dot: 'finished', unseen: true });
    expect(sidebarDot(state({ needsYou: true }), local({ streaming: true }))).toEqual({ dot: 'needsYou' });
  });

  it('D10 (CJFIX-A F2): an ask this tab’s own stream holds open is needs you over its spinner, before the list says so', () => {
    expect(sidebarDot(undefined, { ...local({ streaming: true }), waiting: true })).toEqual({ dot: 'needsYou' });
    expect(sidebarDot(state({ runState: 'working' }), { ...local({ active: true }), waiting: true })).toEqual({ dot: 'needsYou' });
    expect(sidebarDot(undefined, { ...local({ streaming: true }), waiting: false })).toEqual({ dot: 'working' });
  });

  it('shows no finished mark on the chat open now, but still needs you and working', () => {
    expect(sidebarDot(state({ finishedUnseen: true }), local({ active: true, unread: true }))).toBeNull();
    expect(sidebarDot(state({ needsYou: true }), local({ active: true }))).toEqual({ dot: 'needsYou' });
    expect(sidebarDot(state({ runState: 'working' }), local({ active: true }))).toEqual({ dot: 'working' });
  });
});
