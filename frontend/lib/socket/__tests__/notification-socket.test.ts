import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockSocket = {
  connected: false,
  on: vi.fn(),
  off: vi.fn(),
  disconnect: vi.fn(),
  removeAllListeners: vi.fn(),
};

vi.mock('socket.io-client', () => ({
  io: vi.fn(() => mockSocket),
}));

describe('notification-socket', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    mockSocket.connected = false;
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('connects with Bearer auth and default path', async () => {
    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:3100' },
    });
    const { io } = await import('socket.io-client');
    const { connectNotificationSocket, disconnectNotificationSocket } = await import(
      '@/lib/socket/notification-socket'
    );

    connectNotificationSocket('abc');
    expect(io).toHaveBeenCalledWith(
      'http://localhost:3100',
      expect.objectContaining({
        path: '/socket.io',
        auth: { token: 'Bearer abc' },
      }),
    );

    disconnectNotificationSocket();
  });

  it('uses NEXT_PUBLIC_API_BASE_URL when set', async () => {
    const prev = process.env.NEXT_PUBLIC_API_BASE_URL;
    process.env.NEXT_PUBLIC_API_BASE_URL = 'https://api.example.com/';
    vi.stubGlobal('window', {
      location: { origin: 'http://localhost:3100' },
    });
    const { io } = await import('socket.io-client');
    const { connectNotificationSocket, disconnectNotificationSocket } = await import(
      '@/lib/socket/notification-socket'
    );

    connectNotificationSocket('tok');
    expect(io).toHaveBeenCalledWith(
      'https://api.example.com',
      expect.any(Object),
    );
    disconnectNotificationSocket();
    process.env.NEXT_PUBLIC_API_BASE_URL = prev;
  });
});
