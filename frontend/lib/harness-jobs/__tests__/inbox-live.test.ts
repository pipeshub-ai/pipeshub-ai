import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const handlers = new Map<string, (payload: unknown) => void>();
const socket = {
  on: vi.fn((event: string, fn: (payload: unknown) => void) => handlers.set(event, fn)),
  off: vi.fn(),
};
const count = vi.fn();

vi.mock('@/config', () => ({
  useAuthStore: (select: (s: { accessToken: string; isAuthenticated: boolean }) => unknown) =>
    select({ accessToken: 't', isAuthenticated: true }),
}));
vi.mock('@/lib/socket/notification-socket', () => ({ getNotificationSocket: () => socket }));
vi.mock('../inbox-api', () => ({ InboxApi: { count: () => count() } }));

const { useInboxLive } = await import('../use-inbox-live');
const { useInboxStore } = await import('../inbox-store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');

const counts = (over: Record<string, unknown> = {}) => ({ unread: 1, waiting: 0, badge: 1, paused: false, pausedUntil: null, ...over });

describe('the Inbox count push (FIXMS1-FU-1)', () => {
  beforeEach(() => {
    handlers.clear();
    count.mockReset();
    useFeatureFlagsStore.setState({ agentHarness: { useHarness: true, harnessOn: true } } as never);
  });

  it('reads nothing and listens to nothing while the harness is off for the org (CJ-1)', () => {
    useFeatureFlagsStore.setState({ agentHarness: { useHarness: false, harnessOn: false } } as never);
    const { unmount } = renderHook(() => useInboxLive());
    expect(count).not.toHaveBeenCalled();
    expect(handlers.size).toBe(0);
    expect(useInboxStore.getState().counts.waiting).toBe(0);
    unmount();
  });

  it('takes a whole count as pushed, and reads the count itself when the push asks for a recount', async () => {
    count.mockResolvedValue(counts({ waiting: 2, badge: 3 }));
    renderHook(() => useInboxLive());
    await vi.waitFor(() => expect(count).toHaveBeenCalledTimes(1));

    handlers.get('inboxCount')?.(counts({ unread: 4, badge: 4 }));
    expect(useInboxStore.getState().counts?.badge).toBe(4);
    expect(count).toHaveBeenCalledTimes(1);

    count.mockResolvedValue(counts({ waiting: 5, badge: 6 }));
    handlers.get('inboxCount')?.(counts({ unread: 1, badge: 1, recount: true }));
    await vi.waitFor(() => expect(useInboxStore.getState().counts?.badge).toBe(6));
    expect(count).toHaveBeenCalledTimes(2);
  });
});
