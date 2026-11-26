import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * CJ-1 (plan §6): a followed job that ends while the page watches re-reads the sidebar and the Jobs list, a replay of
 * an old job does not; and the open chat gets one seen mark per open, plus one when its run ends there.
 */

const refresh = vi.hoisted(() => vi.fn());
vi.mock('../swr-keys', async (load) => ({ ...(await load<typeof import('../swr-keys')>()), refreshJobLists: refresh }));

type Handlers = { onEvent: (e: unknown) => void; onError: (e: Error) => void };
const streams = vi.hoisted(() => [] as Array<(h: Handlers) => void>);
vi.mock('@/lib/api', async (load) => ({
  ...(await load<typeof import('@/lib/api')>()),
  streamSSEGet: async (_url: string, h: Handlers) => (streams.shift() ?? (() => undefined))(h),
}));

const markSeen = vi.hoisted(() => vi.fn(async () => ({ seenUpTo: 42 })));
vi.mock('../jobs-api', async (load) => {
  const real = await load<typeof import('../jobs-api')>();
  return { ...real, JobsApi: { ...real.JobsApi, markSeen: () => markSeen(), timeline: async () => ({}) } };
});

const params = vi.hoisted(() => ({ value: new URLSearchParams() }));
vi.mock('next/navigation', () => ({ useSearchParams: () => params.value }));

const { followJob, useJobsStore } = await import('../jobs-store');
const { useOpenChatSeenMark } = await import('../use-seen-marks');
const { useChatStore } = await import('@/chat/store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');

const frame = (runId: string, type: string, at: number, id: string) => ({ id, data: { type, runId, timestamp: at } });
const session = { sessionId: 'c1' };

beforeEach(() => {
  refresh.mockClear();
  markSeen.mockClear();
  streams.length = 0;
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} });
});

describe('a followed job’s end', () => {
  it('re-reads the lists once when the job ends while followed', async () => {
    const now = Date.now();
    streams.push((h) => {
      h.onEvent(frame('j1', 'RUN_STARTED', now - 1_000, '1'));
      h.onEvent(frame('j1', 'RUN_FINISHED', now + 1_000, '2'));
    });
    const release = followJob(session, 'j1');
    await vi.waitFor(() => expect(useJobsStore.getState().streams.j1?.ended).toBe(true));
    await vi.waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    release();
  });

  it('does not, for an old job whose journal is only replayed on opening its chat', async () => {
    const long = Date.now() - 3_600_000;
    streams.push((h) => {
      h.onEvent(frame('j2', 'RUN_STARTED', long - 60_000, '1'));
      h.onEvent(frame('j2', 'RUN_FINISHED', long, '2'));
    });
    const release = followJob(session, 'j2');
    // The settle read starts in the same step that decides whether to re-read the lists.
    await vi.waitFor(() => expect(useJobsStore.getState().extras.j2?.settled).toBe(true));
    expect(refresh).not.toHaveBeenCalled();
    release();
  });
});

describe('the open chat’s seen mark', () => {
  beforeEach(() => {
    useFeatureFlagsStore.setState({ agentHarness: { useHarness: true, harnessOn: true } } as never);
    useChatStore.setState({
      slots: {},
      conversations: [{ id: 'c1', title: 'Q3', harnessState: { activeRun: null, runState: null, needsYou: false, finishedUnseen: true, seenUpTo: 1 } } as never],
      sharedConversations: [],
      agentConversations: [],
    });
  });

  it('sends one PUT …/seen per open, clears the row’s unseen mark, and none for a page with no chat', async () => {
    params.value = new URLSearchParams('conversationId=c1');
    const { rerender, unmount } = renderHook(() => useOpenChatSeenMark());
    rerender();
    rerender();
    await vi.waitFor(() => expect(useChatStore.getState().conversations[0]?.harnessState?.finishedUnseen).toBe(false));
    expect(markSeen).toHaveBeenCalledTimes(1);
    unmount();

    params.value = new URLSearchParams('');
    renderHook(() => useOpenChatSeenMark());
    expect(markSeen).toHaveBeenCalledTimes(1);
  });

  it('marks again and re-reads the lists when the open chat’s own run ends', async () => {
    params.value = new URLSearchParams('conversationId=c1');
    const slot = useChatStore.getState().createSlot('c1');
    useChatStore.getState().updateSlot(slot, { isStreaming: true });
    renderHook(() => useOpenChatSeenMark());
    await vi.waitFor(() => expect(markSeen).toHaveBeenCalledTimes(1));
    useChatStore.getState().updateSlot(slot, { isStreaming: false });
    await vi.waitFor(() => expect(markSeen).toHaveBeenCalledTimes(2));
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('sends nothing while the harness is off', () => {
    useFeatureFlagsStore.setState({ agentHarness: { useHarness: false, harnessOn: false } } as never);
    params.value = new URLSearchParams('conversationId=c1');
    renderHook(() => useOpenChatSeenMark());
    expect(markSeen).not.toHaveBeenCalled();
  });
});
