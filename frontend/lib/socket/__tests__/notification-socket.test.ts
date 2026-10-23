import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const ioMock = vi.fn();
const disconnectMock = vi.fn();
const removeAllListenersMock = vi.fn();

interface FakeSocket {
  connected: boolean;
  /** socket.io's: false once the server refused the handshake, until `connect()` is called. */
  active: boolean;
  auth: { token: string };
  disconnect: ReturnType<typeof vi.fn>;
  removeAllListeners: ReturnType<typeof vi.fn>;
  connect: ReturnType<typeof vi.fn>;
  on: (event: string, fn: (...a: unknown[]) => void) => void;
  emit: (event: string, ...a: unknown[]) => void;
}

let lastSocket: FakeSocket | null = null;

vi.mock('socket.io-client', () => ({
  io: (...args: unknown[]) => {
    ioMock(...args);
    const options = (args[1] ?? {}) as { auth?: { token: string } };
    const handlers = new Map<string, Array<(...a: unknown[]) => void>>();
    const created: FakeSocket = {
      connected: false,
      active: true,
      auth: options.auth ?? { token: '' },
      disconnect: disconnectMock,
      removeAllListeners: removeAllListenersMock,
      connect: vi.fn(() => {
        created.active = true;
      }),
      on: (event, fn) => handlers.set(event, [...(handlers.get(event) ?? []), fn]),
      emit: (event, ...a) => (handlers.get(event) ?? []).forEach((fn) => fn(...a)),
    };
    lastSocket = created;
    return created;
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

  describe('bug-bash #5: a handshake the server refused', () => {
    afterEach(() => vi.useRealTimers());

    const refuse = (sock: FakeSocket) => {
      sock.active = false;
      sock.emit('connect_error', new Error('Authentication token expired'));
    };

    it('is retried with growing delays instead of leaving the socket dead until a reload', async () => {
      vi.useFakeTimers();
      const { connectNotificationSocket, REFUSED_RETRY_BASE_MS } = await import('../notification-socket');
      connectNotificationSocket('token-a');
      const sock = lastSocket!;
      refuse(sock);
      vi.advanceTimersByTime(REFUSED_RETRY_BASE_MS - 1);
      expect(sock.connect).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(sock.connect).toHaveBeenCalledTimes(1);
      refuse(sock);
      vi.advanceTimersByTime(REFUSED_RETRY_BASE_MS);
      expect(sock.connect).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(REFUSED_RETRY_BASE_MS);
      expect(sock.connect).toHaveBeenCalledTimes(2);
    });

    it('is left to socket.io when it is still reconnecting by itself', async () => {
      vi.useFakeTimers();
      const { connectNotificationSocket } = await import('../notification-socket');
      connectNotificationSocket('token-a');
      lastSocket!.emit('connect_error', new Error('xhr poll error'));
      vi.advanceTimersByTime(60_000);
      expect(lastSocket!.connect).not.toHaveBeenCalled();
    });

    it('is not retried on a socket a new token replaced', async () => {
      vi.useFakeTimers();
      const { connectNotificationSocket } = await import('../notification-socket');
      connectNotificationSocket('token-a');
      const old = lastSocket!;
      refuse(old);
      connectNotificationSocket('token-b');
      vi.advanceTimersByTime(60_000);
      expect(old.connect).not.toHaveBeenCalled();
    });
  });

  describe('ensureNotificationSocketConnected', () => {
    it('reconnects a socket that gave up, and leaves a live or reconnecting one alone', async () => {
      const { connectNotificationSocket, ensureNotificationSocketConnected } = await import('../notification-socket');
      ensureNotificationSocketConnected();
      connectNotificationSocket('token-a');
      const sock = lastSocket!;
      ensureNotificationSocketConnected();
      expect(sock.connect).not.toHaveBeenCalled();
      sock.active = false;
      ensureNotificationSocketConnected();
      expect(sock.connect).toHaveBeenCalledTimes(1);
      sock.connected = true;
      sock.active = false;
      ensureNotificationSocketConnected();
      expect(sock.connect).toHaveBeenCalledTimes(1);
    });
  });
});
