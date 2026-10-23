import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useNotificationSocket } from '../websocket-manager';
import { resetCatchUp } from '../catch-up';
import { useNotificationStore } from '../store';
import type { NotificationListItem } from '../api';

const authState = {
  accessToken: 'jwt-test',
  isAuthenticated: true,
  isHydrated: true,
};

const onMissedMock = vi.fn();
const onStartMock = vi.fn();

vi.mock('@/config', async () => ({
  useAuthStore: (fn: (s: typeof authState) => unknown) => fn(authState),
  logoutAndRedirect: vi.fn(),
  notificationExtension: {
    ...(await vi.importActual<typeof import('../notification-extension')>('../notification-extension'))
      .defaultNotificationExtension,
    onMissedNotifications: (...args: unknown[]) => onMissedMock(...args),
    onUnreadAtStart: (...args: unknown[]) => onStartMock(...args),
  },
}));

const socketHandlers = new Map<string, (...a: unknown[]) => unknown>();
const connectMock = vi.fn(() => ({
  connected: false,
  on: vi.fn((event: string, fn: (...a: unknown[]) => unknown) => socketHandlers.set(event, fn)),
  off: vi.fn(),
}));

const disconnectMock = vi.fn();
const ensureMock = vi.fn();

vi.mock('@/lib/socket/notification-socket', () => ({
  connectNotificationSocket: (...args: unknown[]) => connectMock(...args),
  disconnectNotificationSocket: () => disconnectMock(),
  ensureNotificationSocketConnected: () => ensureMock(),
}));

const getStatsMock = vi.fn(() =>
  Promise.resolve({
    unreadCount: 0,
    readCount: 0,
    archivedCount: 0,
  }),
);

const listMock = vi.fn((..._args: unknown[]) =>
  Promise.resolve({
    notifications: [] as NotificationListItem[],
    cursor: null,
    hasMore: false,
  }),
);

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    NotificationsApi: {
      list: (...args: unknown[]) => listMock(...args),
      getStats: () => getStatsMock(),
    },
  };
});

describe('useNotificationSocket', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    socketHandlers.clear();
    resetCatchUp();
    useNotificationStore.setState({ notifications: [], isPanelOpen: false });
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

  describe('bug-bash #5: nothing pushed while the socket was down is lost', () => {
    const row = (id: string, createdAt: string, over: Partial<NotificationListItem> = {}): NotificationListItem => ({
      _id: id,
      type: 'chat.mentioned',
      status: 'unread',
      createdAt,
      ...over,
    });
    const unreadPage = (rows: NotificationListItem[]) =>
      listMock.mockImplementationOnce(() => Promise.resolve({ notifications: rows, cursor: null, hasMore: false }));
    const connectEvent = async () => {
      await (socketHandlers.get('connect')?.() as Promise<void>);
    };

    it('the first connect only marks what is there; a reconnect hands on the unread rows newer than that, oldest first', async () => {
      renderHook(() => {
        useNotificationSocket();
      });
      unreadPage([row('a', '2026-10-22T13:00:00.000Z')]);
      await connectEvent();
      expect(onMissedMock).not.toHaveBeenCalled();
      expect(listMock).toHaveBeenCalledWith({ status: 'unread' });

      unreadPage([
        row('c', '2026-10-22T13:31:00.000Z'),
        row('b', '2026-10-22T13:30:00.000Z'),
        row('a', '2026-10-22T13:00:00.000Z'),
      ]);
      await connectEvent();
      expect(onMissedMock).toHaveBeenCalledTimes(1);
      expect((onMissedMock.mock.calls[0]![0] as NotificationListItem[]).map((n) => n._id)).toEqual(['b', 'c']);
      expect(useNotificationStore.getState().notifications.map((n) => n._id)).toEqual(['c', 'b']);
      expect(getStatsMock).toHaveBeenCalled();
    });

    it('the first connect after a load, and after signing in again, hands the unread page to onUnreadAtStart once', async () => {
      const { rerender } = renderHook(() => {
        useNotificationSocket();
      });
      const page = [row('b', '2026-10-22T13:30:00.000Z'), row('a', '2026-10-22T13:00:00.000Z')];
      unreadPage(page);
      await connectEvent();
      expect(onStartMock).toHaveBeenCalledTimes(1);
      expect(onStartMock.mock.calls[0]![0]).toEqual(page);
      unreadPage([row('c', '2026-10-22T13:31:00.000Z'), ...page]);
      await connectEvent();
      expect(onStartMock).toHaveBeenCalledTimes(1);
      expect(onMissedMock).toHaveBeenCalledTimes(1);

      authState.isAuthenticated = false;
      rerender();
      authState.isAuthenticated = true;
      rerender();
      unreadPage(page);
      await connectEvent();
      expect(onStartMock).toHaveBeenCalledTimes(2);
    });

    it('a row that already came live is not handed on again', async () => {
      renderHook(() => {
        useNotificationSocket();
      });
      unreadPage([]);
      await connectEvent();
      socketHandlers.get('newNotification')?.(row('live', '2026-10-22T14:00:00.000Z'));
      unreadPage([row('live', '2026-10-22T14:00:00.000Z')]);
      await connectEvent();
      expect(onMissedMock).not.toHaveBeenCalled();
    });

    it('the network coming back, or the tab coming into view, brings back a socket that gave up', () => {
      renderHook(() => {
        useNotificationSocket();
      });
      window.dispatchEvent(new Event('online'));
      expect(ensureMock).toHaveBeenCalledTimes(1);
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
      document.dispatchEvent(new Event('visibilitychange'));
      expect(ensureMock).toHaveBeenCalledTimes(2);
    });
  });
});
