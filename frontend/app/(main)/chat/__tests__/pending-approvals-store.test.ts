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

afterEach(() => {
  vi.useRealTimers();
});

describe('pending approvals store', () => {
  it('excludes the current chat', async () => {
    api.fetchPendingApprovals.mockResolvedValue([item('a', 'conv-here'), item('b', 'conv-other')]);
    await act(() => usePendingApprovals.getState().refresh());
    const { result } = renderHook(() => useOtherChatApprovals());
    expect(result.current.map((i) => i.card.approvalId)).toEqual(['b']);
  });

  it('polls every 30s only while non-empty', async () => {
    const { unmount } = renderHook(() => usePendingApprovalsSync());
    await act(async () => {});
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(api.fetchPendingApprovals).toHaveBeenCalledTimes(1);

    api.fetchPendingApprovals.mockResolvedValue([item('b', 'conv-other')]);
    await act(() => usePendingApprovals.getState().refresh());
    const before = api.fetchPendingApprovals.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.fetchPendingApprovals.mock.calls.length).toBe(before + 1);
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
});
