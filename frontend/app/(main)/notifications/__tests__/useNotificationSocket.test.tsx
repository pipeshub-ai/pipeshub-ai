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

const connectMock = vi.fn(() => ({
  connected: false,
  on: vi.fn(),
  off: vi.fn(),
}));

const disconnectMock = vi.fn();

vi.mock('@/lib/socket/notification-socket', () => ({
  connectNotificationSocket: (...args: unknown[]) => connectMock(...args),
  disconnectNotificationSocket: () => disconnectMock(),
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    NotificationsApi: {
      list: () =>
        Promise.resolve({
          notifications: [],
          cursor: null,
          hasMore: false,
          unreadCount: 0,
        }),
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
});
