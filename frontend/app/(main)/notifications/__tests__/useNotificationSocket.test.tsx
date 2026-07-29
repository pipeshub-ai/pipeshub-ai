import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useNotificationSocket } from '../websocket-manager';

const authState = {
  accessToken: 'jwt-test',
  isAuthenticated: true,
  isHydrated: true,
};

vi.mock('@/lib/store/auth-store', () => ({
  useAuthStore: (fn: (s: typeof authState) => unknown) => fn(authState),
}));

const connectMock = vi.fn(() => {
  const handlers = new Map<string, (payload: unknown) => void>();
  return {
    connected: false,
    on: vi.fn((event: string, handler: (payload: unknown) => void) => {
      handlers.set(event, handler);
    }),
    off: vi.fn(),
    emitNewNotification: (payload: unknown) => {
      handlers.get('newNotification')?.(payload);
    },
  };
});

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

const showDesktopNotificationMock = vi.fn();

vi.mock('../browser-notifications', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../browser-notifications')>();
  return {
    ...actual,
    showDesktopNotification: (...args: unknown[]) =>
      showDesktopNotificationMock(...args),
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

  it('forwards new socket notifications to desktop notifications', () => {
    renderHook(() => {
      useNotificationSocket();
    });

    const socket = connectMock.mock.results[0]?.value as {
      emitNewNotification: (payload: unknown) => void;
    };
    const payload = {
      _id: 'n-42',
      type: 'test',
      title: 'Hello',
      status: 'unread',
    };

    socket.emitNewNotification(payload);

    expect(showDesktopNotificationMock).toHaveBeenCalledWith(payload);
  });
});
