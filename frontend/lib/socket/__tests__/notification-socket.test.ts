import { describe, it, expect, vi, beforeEach } from 'vitest';

const ioMock = vi.fn();
const disconnectMock = vi.fn();
const removeAllListenersMock = vi.fn();

let lastSocket: {
  connected: boolean;
  auth: { token: string };
  disconnect: ReturnType<typeof vi.fn>;
  removeAllListeners: ReturnType<typeof vi.fn>;
} | null = null;

vi.mock('socket.io-client', () => ({
  io: (...args: unknown[]) => {
    ioMock(...args);
    const options = (args[1] ?? {}) as { auth?: { token: string } };
    lastSocket = {
      connected: false,
      auth: options.auth ?? { token: '' },
      disconnect: disconnectMock,
      removeAllListeners: removeAllListenersMock,
    };
    return lastSocket;
  },
}));

const apiBaseUrlMock = vi.fn(() => '');
vi.mock('@/lib/utils/api-base-url', () => ({ getApiBaseUrl: () => apiBaseUrlMock() }));

describe('notification-socket', () => {
  beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();
    lastSocket = null;
    apiBaseUrlMock.mockReturnValue('');
    const mod = await import('../notification-socket');
    mod.disconnectNotificationSocket();
  });

  it('creates a socket with bearer auth for the given token', async () => {
    const { connectNotificationSocket } = await import('../notification-socket');
    connectNotificationSocket('token-a');

    expect(ioMock).toHaveBeenCalledTimes(1);
    expect(lastSocket?.auth).toEqual({ token: 'Bearer token-a' });
  });

  it('reuses the socket when still connected with the same token', async () => {
    const { connectNotificationSocket } = await import('../notification-socket');
    const first = connectNotificationSocket('token-a');
    if (lastSocket) lastSocket.connected = true;

    const second = connectNotificationSocket('token-a');

    expect(second).toBe(first);
    expect(ioMock).toHaveBeenCalledTimes(1);
  });

  it('recreates the socket when the access token changes', async () => {
    const { connectNotificationSocket } = await import('../notification-socket');
    connectNotificationSocket('token-a');
    if (lastSocket) lastSocket.connected = true;

    connectNotificationSocket('token-b');

    expect(disconnectMock).toHaveBeenCalled();
    expect(ioMock).toHaveBeenCalledTimes(2);
    expect(lastSocket?.auth).toEqual({ token: 'Bearer token-b' });
  });

  it('connects to the configured API server, not the page origin (desktop app:// origin)', async () => {
    apiBaseUrlMock.mockReturnValue('http://localhost:14000/');
    const { connectNotificationSocket } = await import('../notification-socket');
    connectNotificationSocket('token-a');

    expect(ioMock).toHaveBeenCalledWith(
      'http://localhost:14000',
      expect.objectContaining({ path: '/socket.io' })
    );
  });

  it('falls back to the page origin when no API base URL is configured', async () => {
    const { connectNotificationSocket } = await import('../notification-socket');
    connectNotificationSocket('token-a');

    expect(ioMock).toHaveBeenCalledWith(
      window.location.origin,
      expect.objectContaining({ path: '/socket.io' })
    );
  });

  it('keeps a server sub-path in the engine path, not the namespace', async () => {
    apiBaseUrlMock.mockReturnValue('https://host.example.com/pipes/');
    const { connectNotificationSocket } = await import('../notification-socket');
    connectNotificationSocket('token-a');

    expect(ioMock).toHaveBeenCalledWith(
      'https://host.example.com',
      expect.objectContaining({ path: '/pipes/socket.io' })
    );
  });
});
