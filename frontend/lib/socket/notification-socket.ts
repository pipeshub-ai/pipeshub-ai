import { io, type Socket } from 'socket.io-client';

let socket: Socket | null = null;
/** Access token used for the current socket handshake (detect stale reuse). */
let connectedWithToken: string | null = null;

type SocketHandler = (...args: unknown[]) => void;

/**
 * Event listeners owned by the app rather than by a particular socket
 * instance. The socket is torn down and rebuilt on every token refresh, and
 * `getNotificationSocket()` is null before the first connect, so a component
 * that attaches directly to the instance either misses the connection
 * entirely or goes deaf the first time the token rotates. Everything here is
 * re-attached to each new socket.
 */
const subscriptions = new Set<{ event: string; handler: SocketHandler }>();

function attach(sock: Socket, sub: { event: string; handler: SocketHandler }): void {
  sock.on(sub.event, sub.handler);
}

/**
 * Listen for `event` for as long as the returned function is not called,
 * across socket reconnects and token refreshes.
 */
export function subscribeToNotificationEvent<T = unknown>(
  event: string,
  handler: (payload: T) => void,
): () => void {
  const sub = { event, handler: handler as SocketHandler };
  subscriptions.add(sub);
  if (socket) attach(socket, sub);
  return () => {
    subscriptions.delete(sub);
    socket?.off(sub.event, sub.handler);
  };
}

function getSocketBaseUrl(): string {
  if (typeof window === 'undefined') return '';
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (base && base.length > 0) {
    return base.replace(/\/$/, '');
  }
  return window.location.origin;
}

function bearerAuth(accessToken: string): { token: string } {
  return { token: `Bearer ${accessToken}` };
}

function teardownSocket(): void {
  if (socket) {
    socket.disconnect();
    socket.removeAllListeners();
    socket = null;
  }
  connectedWithToken = null;
}

export function getNotificationSocket(): Socket | null {
  return socket;
}

export function connectNotificationSocket(accessToken: string | null): Socket | null {
  if (typeof window === 'undefined') return null;
  if (!accessToken) return null;

  // Reuse only when still connected with the same token.
  if (socket?.connected && connectedWithToken === accessToken) {
    return socket;
  }

  const url = getSocketBaseUrl();
  if (!url) return null;

  teardownSocket();

  socket = io(url, {
    path: '/socket.io',
    transports: ['websocket', 'polling'],
    auth: bearerAuth(accessToken),
    autoConnect: true,
  });
  connectedWithToken = accessToken;
  subscriptions.forEach((sub) => attach(socket as Socket, sub));
  return socket;
}

export function disconnectNotificationSocket(): void {
  teardownSocket();
}
