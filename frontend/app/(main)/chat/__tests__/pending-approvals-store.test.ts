import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { PendingApprovalItem } from '../types';

const api = vi.hoisted(() => ({ fetchPendingApprovals: vi.fn() }));
vi.mock('../api', () => ({ fetchPendingApprovals: api.fetchPendingApprovals }));

const chat = vi.hoisted(() => ({
  state: { activeSlotId: 'slot-1' as string | null, slots: { 'slot-1': { convId: 'conv-here' } } as Record<string, { convId: string | null }> },
}));
vi.mock('../store', () => ({
  useChatStore: Object.assign((selector: (s: typeof chat.state) => unknown) => selector(chat.state), {
    getState: () => chat.state,
  }),
}));

import { useAuthStore } from '@/config';
import {
  resetPendingApprovalsForTest,
  useOtherChatApprovals,
  usePendingApprovals,
  usePendingApprovalsSync,
} from '../pending-approvals-store';

function item(id: string, conversationId: string, expiresIn = 15 * 60_000): PendingApprovalItem {
  return {
    conversationId,
    conversationTitle: `Chat ${conversationId}`,
    agentId: null,
    createdAt: Date.now(),
    card: { approvalId: id, instanceId: 'i', serverName: 'Jira', toolName: 'create_issue', expiresAt: Date.now() + expiresIn },
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  resetPendingApprovalsForTest();
  api.fetchPendingApprovals.mockReset();
  api.fetchPendingApprovals.mockResolvedValue([]);
});

let visibility: DocumentVisibilityState = 'visible';
let visibilitySpy: { mockRestore: () => void } | undefined;
function setVisibility(state: DocumentVisibilityState) {
  visibility = state;
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  visibility = 'visible';
  visibilitySpy = vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
});

afterEach(() => {
  vi.useRealTimers();
  visibilitySpy?.mockRestore();
});

describe('pending approvals store', () => {
  it('excludes the current chat', async () => {
    api.fetchPendingApprovals.mockResolvedValue([item('a', 'conv-here'), item('b', 'conv-other')]);
    await act(() => usePendingApprovals.getState().refresh());
    const { result } = renderHook(() => useOtherChatApprovals());
    expect(result.current.map((i) => i.card.approvalId)).toEqual(['b']);
  });

  it('polls every 30s while the page is visible, even with nothing waiting', async () => {
    const { unmount } = renderHook(() => usePendingApprovalsSync());
    await act(async () => {});
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(2);

    // A call asked in another chat shows up with the next poll.
    api.fetchPendingApprovals.mockResolvedValue([item('b', 'conv-other')]);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(usePendingApprovals.getState().items.map((i) => i.card.approvalId)).toEqual(['b']);
    unmount();
  });

  it('pauses while the page is hidden and refreshes when it is shown again', async () => {
    const { unmount } = renderHook(() => usePendingApprovalsSync());
    await act(async () => {});
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);

    setVisibility('hidden');
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);

    setVisibility('visible');
    await act(async () => {});
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(3);
    unmount();
  });

  it('a poll tick while a slow fetch runs waits for it instead of discarding it', async () => {
    let finish!: (items: PendingApprovalItem[]) => void;
    api.fetchPendingApprovals.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const { unmount } = renderHook(() => usePendingApprovalsSync());
    await act(async () => {});
    // Slower than the poll: the tick, and a focus, start no second fetch.
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);
    await act(async () => { finish([item('slow', 'conv-other')]); });
    expect(usePendingApprovals.getState().items.map((i) => i.card.approvalId)).toEqual(['slow']);
    // Polling carries on once it is back.
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(2);
    unmount();
  });

  it('a put during a slow polled fetch still discards that fetch', async () => {
    let finish!: (items: PendingApprovalItem[]) => void;
    api.fetchPendingApprovals.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const { unmount } = renderHook(() => usePendingApprovalsSync());
    await act(async () => {});
    const refused = { ...item('b', 'conv-other'), card: { ...item('b', 'conv-other').card, editErrors: [{ path: '', message: 'No' }] } };
    act(() => usePendingApprovals.getState().put(refused));
    await act(async () => { finish([item('b', 'conv-other')]); });
    expect(usePendingApprovals.getState().items).toEqual([refused]);
    unmount();
  });

  it('expired item leaves on its own', async () => {
    api.fetchPendingApprovals.mockResolvedValue([item('soon', 'conv-other', 5_000), item('later', 'conv-x')]);
    const { unmount } = renderHook(() => usePendingApprovalsSync());
    await act(async () => {});
    expect(usePendingApprovals.getState().items).toHaveLength(2);
    const calls = api.fetchPendingApprovals.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5_001); });
    expect(usePendingApprovals.getState().items.map((i) => i.card.approvalId)).toEqual(['later']);
    expect(api.fetchPendingApprovals.mock.calls.length).toBe(calls);
    unmount();
  });

  it('refreshes on window focus', async () => {
    const { unmount } = renderHook(() => usePendingApprovalsSync());
    await act(async () => {});
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(2);
    unmount();
  });

  it('a dropped call stays out of a list fetched before the server claimed it', async () => {
    api.fetchPendingApprovals.mockResolvedValue([item('b', 'conv-other')]);
    await act(() => usePendingApprovals.getState().refresh());
    act(() => usePendingApprovals.getState().drop('b'));
    await act(() => usePendingApprovals.getState().refresh());
    expect(usePendingApprovals.getState().items).toEqual([]);
  });

  it('a refresh that started before a put does not overwrite the card it put back', async () => {
    let finish!: (items: PendingApprovalItem[]) => void;
    api.fetchPendingApprovals.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const inFlight = usePendingApprovals.getState().refresh();
    const refused = { ...item('b', 'conv-other'), card: { ...item('b', 'conv-other').card, editErrors: [{ path: '', message: 'No' }] } };
    act(() => usePendingApprovals.getState().put(refused));
    finish([item('b', 'conv-other')]);
    await act(() => inFlight);
    expect(usePendingApprovals.getState().items).toEqual([refused]);
  });

  it('an older refresh that finishes last does not overwrite a newer one', async () => {
    let finishOld!: (items: PendingApprovalItem[]) => void;
    api.fetchPendingApprovals
      .mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve; }))
      .mockResolvedValueOnce([item('new', 'conv-other')]);
    const older = usePendingApprovals.getState().refresh();
    await act(() => usePendingApprovals.getState().refresh());
    finishOld([item('old', 'conv-other')]);
    await act(() => older);
    expect(usePendingApprovals.getState().items.map((i) => i.card.approvalId)).toEqual(['new']);
  });

  describe('signing out', () => {
    beforeEach(() => {
      useAuthStore.getState().setTokens('access', 'refresh');
    });
    afterEach(() => {
      useAuthStore.getState().logout();
      useAuthStore.getState().setUser(null);
    });

    const hanging = () => {
      let finish!: (items: PendingApprovalItem[]) => void;
      api.fetchPendingApprovals.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
      return (items: PendingApprovalItem[]) => finish(items);
    };

    it('empties the list when the person signs out', async () => {
      api.fetchPendingApprovals.mockResolvedValue([item('b', 'conv-other')]);
      await act(() => usePendingApprovals.getState().refresh());
      act(() => useAuthStore.getState().logout());
      expect(usePendingApprovals.getState().items).toEqual([]);
    });

    it('discards a fetch that was running when the list was reset', async () => {
      const finish = hanging();
      const running = usePendingApprovals.getState().refresh();
      act(() => usePendingApprovals.getState().reset());
      finish([item('old-user', 'conv-other')]);
      await act(() => running);
      expect(usePendingApprovals.getState().items).toEqual([]);
    });

    it("starts the next person's refresh at once, not after the old fetch", async () => {
      hanging();
      const old = renderHook(() => usePendingApprovalsSync());
      await act(async () => {});
      old.unmount();
      act(() => useAuthStore.getState().logout());

      api.fetchPendingApprovals.mockResolvedValue([item('mine', 'conv-other')]);
      const next = renderHook(() => usePendingApprovalsSync());
      await act(async () => {});
      expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(2);
      expect(usePendingApprovals.getState().items.map((i) => i.card.approvalId)).toEqual(['mine']);
      next.unmount();
    });

    it('resets when the active workspace changes, in a store that keeps one', async () => {
      api.fetchPendingApprovals.mockResolvedValue([item('b', 'conv-other')]);
      await act(() => usePendingApprovals.getState().refresh());
      act(() => useAuthStore.setState({ currentOrgId: 'org-2' } as never));
      expect(usePendingApprovals.getState().items).toEqual([]);
      act(() => useAuthStore.setState({ currentOrgId: undefined } as never));
    });

    it('resets when a different person signs in', async () => {
      act(() => useAuthStore.getState().setUser({ id: 'u1' }));
      api.fetchPendingApprovals.mockResolvedValue([item('b', 'conv-other')]);
      await act(() => usePendingApprovals.getState().refresh());
      act(() => useAuthStore.getState().setUser({ id: 'u1', name: 'Ann' }));
      expect(usePendingApprovals.getState().items).toHaveLength(1);
      act(() => useAuthStore.getState().setUser({ id: 'u2' }));
      expect(usePendingApprovals.getState().items).toEqual([]);
    });
  });
});
