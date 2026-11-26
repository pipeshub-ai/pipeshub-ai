/**
 * FIX-NAV: moving between chats showed "Agent run not found". The runtime takes the switched-to chat's messages in an
 * effect after the render that switched, so that render drew the chat just left under the new chat's id, and its last
 * answer read `…/conversations/<new>/runs/<old run>/timeline`. These tests use the real assistant-ui runtime and the
 * chat's own adapter config, as the chat page does.
 */
import React, { useMemo } from 'react';
import { act, cleanup, render, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AssistantRuntimeProvider,
  useAuiState,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react';
import '@/lib/__tests__/test-i18n';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn() }, streamSSEGet: vi.fn() }));

import { apiClient } from '@/lib/api';
import { useChatStore } from '../store';
import { buildExternalStoreConfig } from '../runtime';
import { messageRunId } from '../activity';
import { ThreadOwnerContext, threadOwnerOf } from '../hooks/use-thread-owner';
import { useDurableSession, useRunTimeline } from '../hooks/use-run-timeline';
import { useMessageRun } from '../components/activity-panel/use-activity-run';
import { CloneUnavailableCards } from '../components/message-area/outside-access';
import type { RunTimelineView } from '../types';

const get = vi.mocked(apiClient.get);

function answered(convId: string, runId: string): ThreadMessageLike[] {
  return [
    { id: `${convId}-q`, role: 'user', content: [{ type: 'text', text: 'question' }] },
    {
      id: `${convId}-a`,
      role: 'assistant',
      content: [{ type: 'text', text: 'answer' }],
      metadata: { custom: { durableRunId: runId } },
    },
  ];
}

/** The latest answer as the chat draws it: its run, then the reads keyed by it. */
function LastAnswer({ custom }: { custom: unknown }) {
  const run = useMessageRun(messageRunId(custom) ?? undefined, true);
  const session = useDurableSession();
  return (
    <>
      <CloneUnavailableCards run={run} />
      <span data-testid="session">{session?.sessionId ?? ''}</span>
    </>
  );
}

/** The message list's part of the fix: the thread's owner from the same snapshot as its messages. */
function Thread({ guarded }: { guarded: boolean }) {
  const messages = useAuiState((s) => s.thread.messages);
  const extras = useAuiState((s) => s.thread.extras);
  const owner = useMemo(() => threadOwnerOf(extras), [extras]);
  const last = [...messages].reverse().find((m) => m.role === 'assistant');
  const body = last ? <LastAnswer custom={last.metadata?.custom} /> : null;
  return guarded ? <ThreadOwnerContext.Provider value={owner}>{body}</ThreadOwnerContext.Provider> : body;
}

/** The chat page: one external-store runtime over the active slot. */
function Page({ guarded = true }: { guarded?: boolean }) {
  const activeSlotId = useChatStore((s) => s.activeSlotId);
  const messages = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.messages : undefined));
  const convId = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.convId : null));
  // As the chat page: the config reads the store itself; these only say when to build it again.
  const config = useMemo(() => buildExternalStoreConfig(activeSlotId), [activeSlotId, messages, convId]);
  const runtime = useExternalStoreRuntime(config);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread guarded={guarded} />
    </AssistantRuntimeProvider>
  );
}

const timelineUrls = () =>
  get.mock.calls.map(([url]) => String(url)).filter((url) => url.includes('/runs/') && url.endsWith('/timeline'));

function twoChats() {
  const store = useChatStore.getState();
  const slotA = store.createSlot('conv-a');
  const slotB = store.createSlot('conv-b');
  store.updateSlot(slotA, { messages: answered('conv-a', 'run-a1'), hasLoaded: true, isInitialized: true });
  store.updateSlot(slotB, { messages: answered('conv-b', 'run-b1'), hasLoaded: true, isInitialized: true });
  store.setActiveSlot(slotA);
  return { slotA, slotB };
}

function timeline(runId: string): RunTimelineView {
  return { runId, status: 'completed', parts: [] } as unknown as RunTimelineView;
}

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async (url: string) => {
    const runId = /\/runs\/([^/]+)\/timeline$/.exec(url)?.[1] ?? 'x';
    return { data: timeline(decodeURIComponent(runId)), status: 200 };
  });
});

afterEach(() => {
  cleanup();
  useChatStore.setState(useChatStore.getInitialState(), true);
});

describe('FIX-NAV: switching chats never pairs one chat with another chat\'s run', () => {
  it('without the thread owner, the runtime really draws chat A under chat B for one render (the race)', async () => {
    const { slotB } = twoChats();
    render(<Page guarded={false} />);
    await waitFor(() => expect(timelineUrls()).toContainEqual(expect.stringContaining('conv-a/runs/run-a1/timeline')));
    act(() => useChatStore.getState().setActiveSlot(slotB));
    await waitFor(() => expect(timelineUrls()).toContainEqual(expect.stringContaining('conv-b/runs/run-b1/timeline')));
    expect(timelineUrls()).toContainEqual(expect.stringContaining('conv-b/runs/run-a1/timeline'));
  });

  it('switching A (run a1) to B quickly sends no request pairing B with a1, and back again none pairing A with b1', async () => {
    const { slotA, slotB } = twoChats();
    const { getByTestId } = render(<Page />);
    await waitFor(() => expect(timelineUrls()).toContainEqual(expect.stringContaining('conv-a/runs/run-a1/timeline')));
    act(() => useChatStore.getState().setActiveSlot(slotB));
    act(() => useChatStore.getState().setActiveSlot(slotA));
    act(() => useChatStore.getState().setActiveSlot(slotB));
    await waitFor(() => expect(timelineUrls()).toContainEqual(expect.stringContaining('conv-b/runs/run-b1/timeline')));
    await waitFor(() => expect(getByTestId('session').textContent).toBe('conv-b'));
    const urls = timelineUrls();
    expect(urls.some((url) => url.includes('conv-b/runs/run-a1'))).toBe(false);
    expect(urls.some((url) => url.includes('conv-a/runs/run-b1'))).toBe(false);
  });

  it('the timeline is a background read: it never asks for the global error toast', async () => {
    const { ChatApi } = await import('../api');
    await ChatApi.getRunTimeline({ sessionId: 'conv-a', runId: 'run-a1' });
    expect(get).toHaveBeenCalledWith(expect.stringContaining('/runs/run-a1/timeline'), expect.objectContaining({ suppressErrorToast: true }));
  });

  it("a late reply for A's run does not land in B's view, and a late failure does not mark B failed", async () => {
    let answerA: (view: { data: RunTimelineView }) => void = () => undefined;
    let failA: (error: Error) => void = () => undefined;
    get.mockImplementation(
      (url: string) =>
        new Promise((resolve, reject) => {
          if (url.includes('run-a1')) {
            answerA = resolve as typeof answerA;
            failA = reject;
          } else resolve({ data: timeline('run-b1'), status: 200 });
        }) as never,
    );
    const { result, rerender } = renderHook(
      ({ sessionId, runId }: { sessionId: string; runId: string }) => useRunTimeline({ sessionId }, runId, { enabled: true }),
      { initialProps: { sessionId: 'conv-a', runId: 'run-a1' } },
    );
    rerender({ sessionId: 'conv-b', runId: 'run-b1' });
    await waitFor(() => expect(result.current.timeline?.runId).toBe('run-b1'));
    await act(async () => {
      answerA({ data: timeline('run-a1') });
      failA(new Error('Agent run not found'));
    });
    expect(result.current.timeline?.runId).toBe('run-b1');
    expect(result.current.failed).toBe(false);
  });

  it('a chat whose own run is gone shows nothing and is not marked failed for another run', async () => {
    get.mockRejectedValue(new Error('Agent run not found'));
    const { result, rerender } = renderHook(
      ({ runId }: { runId: string }) => useRunTimeline({ sessionId: 'conv-a' }, runId, { enabled: true }),
      { initialProps: { runId: 'run-gone' } },
    );
    await waitFor(() => expect(result.current.failed).toBe(true));
    expect(result.current.timeline).toBeNull();
    get.mockResolvedValue({ data: timeline('run-a2'), status: 200 });
    rerender({ runId: 'run-a2' });
    expect(result.current.failed).toBe(false);
    await waitFor(() => expect(result.current.timeline?.runId).toBe('run-a2'));
  });
});
