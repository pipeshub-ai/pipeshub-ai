import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useNotificationStore } from '../store';
import { useNotificationSocket } from '../websocket-manager';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';

const authState = {
  accessToken: 'jwt-test',
  isAuthenticated: true,
  isHydrated: true,
};

vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: typeof authState) => unknown) => fn(authState),
  logoutAndRedirect: vi.fn(),
}));

const socketHandlers: Record<string, (payload: unknown) => void> = {};
const connectMock = vi.fn(() => ({
  connected: false,
  on: vi.fn((event: string, fn: (payload: unknown) => void) => {
    socketHandlers[event] = fn;
  }),
  off: vi.fn(),
}));

const disconnectMock = vi.fn();

vi.mock('@/lib/socket/notification-socket', () => ({
  connectNotificationSocket: (...args: unknown[]) => connectMock(...args),
  disconnectNotificationSocket: () => disconnectMock(),
}));

const getStatsMock = vi.fn(() =>
  Promise.resolve({
    unreadCount: 0,
    readCount: 0,
    archivedCount: 0,
  }),
);

const listMock = vi.fn(() =>
  Promise.resolve({
    notifications: [],
    cursor: null,
    hasMore: false,
  }),
);

const getPreferencesMock = vi.fn((_opts?: unknown) => Promise.resolve({ mutedSessions: ['s1', 's2'] }));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    NotificationsApi: {
      list: (...args: unknown[]) => listMock(...args),
      getStats: () => getStatsMock(),
      getPreferences: (opts?: unknown) => getPreferencesMock(opts),
    },
  };
});

describe('useNotificationSocket', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.accessToken = 'jwt-test';
    authState.isAuthenticated = true;
    authState.isHydrated = true;
  });

  it('connects when authenticated and hydrated', () => {
    renderHook(() => {
      useNotificationSocket();
    });
    expect(connectMock).toHaveBeenCalledWith('jwt-test');
  });

  it('disconnects on unmount', () => {
    const { unmount } = renderHook(() => {
      useNotificationSocket();
    });
    unmount();
    expect(disconnectMock).toHaveBeenCalled();
  });

  it('refetches stats when the tab becomes visible', async () => {
    renderHook(() => {
      useNotificationSocket();
    });

    getStatsMock.mockClear();

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));

    await vi.waitFor(() => {
      expect(getStatsMock).toHaveBeenCalled();
    });
  });

  it('reconnects with a new token when accessToken changes', () => {
    const { rerender } = renderHook(() => {
      useNotificationSocket();
    });

    expect(connectMock).toHaveBeenCalledWith('jwt-test');

    authState.accessToken = 'jwt-rotated';
    rerender();

    expect(connectMock).toHaveBeenCalledWith('jwt-rotated');
    expect(disconnectMock).toHaveBeenCalled();
  });

  it('applies a notificationUpdated event as an upsert, not a second row', () => {
    useNotificationStore.setState({ notifications: [], unreadCount: 0 });
    renderHook(() => {
      useNotificationSocket();
    });
    const row = { _id: 'n1', type: 'chat.activity', status: 'unread', payload: { sessionId: 's1', count: 1 } };
    act(() => socketHandlers.newNotification(row));
    act(() => socketHandlers.notificationUpdated({ ...row, payload: { sessionId: 's1', count: 5 } }));
    const { notifications, unreadCount } = useNotificationStore.getState();
    expect(notifications).toHaveLength(1);
    expect(notifications[0].payload?.count).toBe(5);
    expect(unreadCount).toBe(1);
  });

  describe('muted sessions for the sidebar marker', () => {
    const setFlag = (on: boolean) =>
      useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as never);

    it('loads them without the panel being opened when collaborative chats are on', async () => {
      setFlag(true);
      useNotificationStore.setState({ mutedSessionIds: [], isPanelOpen: false });
      renderHook(() => {
        useNotificationSocket();
      });
      await vi.waitFor(() => expect(useNotificationStore.getState().mutedSessionIds).toEqual(['s1', 's2']));
    });

    it('the startup load and the first connect share one quiet request', async () => {
      setFlag(true);
      let release: (v: { mutedSessions: string[] }) => void = () => {};
      getPreferencesMock.mockImplementationOnce(() => new Promise((r) => { release = r; }));
      renderHook(() => {
        useNotificationSocket();
      });
      await act(async () => {
        await socketHandlers.connect?.(undefined);
      });
      expect(getPreferencesMock).toHaveBeenCalledTimes(1);
      expect(getPreferencesMock).toHaveBeenCalledWith({ quiet: true });
      await act(async () => release({ mutedSessions: ['s9'] }));
      await vi.waitFor(() => expect(useNotificationStore.getState().mutedSessionIds).toEqual(['s9']));
    });

    it('does not ask for them with the flag off', async () => {
      setFlag(false);
      useNotificationStore.setState({ mutedSessionIds: [] });
      renderHook(() => {
        useNotificationSocket();
      });
      await Promise.resolve();
      expect(getPreferencesMock).not.toHaveBeenCalled();
      expect(useNotificationStore.getState().mutedSessionIds).toEqual([]);
    });
  });
});
