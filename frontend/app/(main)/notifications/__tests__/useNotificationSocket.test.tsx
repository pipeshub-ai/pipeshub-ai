import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { normalizeNotificationPayload, useNotificationSocket } from '../websocket-manager';

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

vi.mock('../api', () => ({
  NotificationsApi: {
    getAll: () => Promise.resolve([]),
  },
}));

describe('normalizeNotificationPayload', () => {
  it('maps mongoose-like assignedTo', () => {
    const item = normalizeNotificationPayload({
      _id: '507f1f77bcf86cd799439011',
      title: 'T',
      type: 'CONNECTOR_ERROR',
      link: '/connectors',
      status: 'Unread',
      assignedTo: { toString: () => '507f191e810c19729de860ea' },
    });
    expect(item._id).toBe('507f1f77bcf86cd799439011');
    expect(item.assignedTo).toBe('507f191e810c19729de860ea');
  });
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

  it('does not connect when not authenticated', () => {
    authState.isAuthenticated = false;
    renderHook(() => {
      useNotificationSocket();
    });
    expect(connectMock).not.toHaveBeenCalled();
  });
});
