import { describe, it, expect, vi, beforeEach } from 'vitest';

const ioMock = vi.fn();
const disconnectMock = vi.fn();
const removeAllListenersMock = vi.fn();

interface FakeSocket {
  connected: boolean;
  auth: { token: string };
  handlers: Map<string, Set<(payload: unknown) => void>>;
  on: (event: string, handler: (payload: unknown) => void) => void;
  off: (event: string, handler: (payload: unknown) => void) => void;
  emitToClient: (event: string, payload: unknown) => void;
  disconnect: ReturnType<typeof vi.fn>;
  removeAllListeners: ReturnType<typeof vi.fn>;
}

let lastSocket: FakeSocket | null = null;

vi.mock('socket.io-client', () => ({
  io: (...args: unknown[]) => {
    ioMock(...args);
    const options = (args[1] ?? {}) as { auth?: { token: string } };
    const handlers = new Map<string, Set<(payload: unknown) => void>>();
    lastSocket = {
      connected: false,
      auth: options.auth ?? { token: '' },
      handlers,
      on: (event, handler) => {
        if (!handlers.has(event)) handlers.set(event, new Set());
        handlers.get(event)!.add(handler);
      },
      off: (event, handler) => {
        handlers.get(event)?.delete(handler);
      },
      emitToClient: (event, payload) => {
        handlers.get(event)?.forEach((handler) => handler(payload));
      },
      disconnect: disconnectMock,
      removeAllListeners: removeAllListenersMock,
    };
    return lastSocket;
  },
}));

describe('notification-socket', () => {
  beforeEach(async () => {
    vi.resetModules();
    vi.clearAllMocks();
    lastSocket = null;
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

  describe('subscriptions', () => {
    it('delivers events to a handler registered before the socket exists', async () => {
      // Components mount before the first connect; attaching to the instance
      // directly would mean they never hear anything.
      const { subscribeToNotificationEvent, connectNotificationSocket } = await import(
        '../notification-socket'
      );
      const handler = vi.fn();
      subscribeToNotificationEvent('workflowRunUpdate', handler);

      connectNotificationSocket('token-a');
      lastSocket?.emitToClient('workflowRunUpdate', { runId: 'run-1' });

      expect(handler).toHaveBeenCalledWith({ runId: 'run-1' });
    });

    it('survives the teardown and rebuild a token refresh performs', async () => {
      const { subscribeToNotificationEvent, connectNotificationSocket } = await import(
        '../notification-socket'
      );
      const handler = vi.fn();
      subscribeToNotificationEvent('workflowRunUpdate', handler);

      connectNotificationSocket('token-a');
      if (lastSocket) lastSocket.connected = true;
      connectNotificationSocket('token-b');

      lastSocket?.emitToClient('workflowRunUpdate', { runId: 'run-2' });

      expect(handler).toHaveBeenCalledWith({ runId: 'run-2' });
    });

    it('stops delivering after unsubscribe', async () => {
      const { subscribeToNotificationEvent, connectNotificationSocket } = await import(
        '../notification-socket'
      );
      const handler = vi.fn();
      const unsubscribe = subscribeToNotificationEvent('workflowRunUpdate', handler);
      connectNotificationSocket('token-a');

      unsubscribe();
      lastSocket?.emitToClient('workflowRunUpdate', { runId: 'run-3' });

      expect(handler).not.toHaveBeenCalled();
    });

    it('an unsubscribed handler is not re-attached to a later socket', async () => {
      const { subscribeToNotificationEvent, connectNotificationSocket } = await import(
        '../notification-socket'
      );
      const handler = vi.fn();
      const unsubscribe = subscribeToNotificationEvent('workflowRunUpdate', handler);
      connectNotificationSocket('token-a');
      unsubscribe();

      if (lastSocket) lastSocket.connected = true;
      connectNotificationSocket('token-b');
      lastSocket?.emitToClient('workflowRunUpdate', { runId: 'run-4' });

      expect(handler).not.toHaveBeenCalled();
    });

    it('keeps multiple subscribers on the same event independent', async () => {
      const { subscribeToNotificationEvent, connectNotificationSocket } = await import(
        '../notification-socket'
      );
      const first = vi.fn();
      const second = vi.fn();
      const unsubscribeFirst = subscribeToNotificationEvent('workflowRunUpdate', first);
      subscribeToNotificationEvent('workflowRunUpdate', second);
      connectNotificationSocket('token-a');

      unsubscribeFirst();
      lastSocket?.emitToClient('workflowRunUpdate', { runId: 'run-5' });

      expect(first).not.toHaveBeenCalled();
      expect(second).toHaveBeenCalledWith({ runId: 'run-5' });
    });
  });
});
