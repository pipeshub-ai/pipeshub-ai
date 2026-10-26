import { io, type Socket } from 'socket.io-client';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

let socket: Socket | null = null;
/** Access token used for the current socket handshake (detect stale reuse). */
let connectedWithToken: string | null = null;

function getSocketBaseUrl(): string {
  if (typeof window === 'undefined') return '';
  // The desktop app's own origin is app://, so the server URL must come from the
  // same source the HTTP client uses.
  const base = getApiBaseUrl();
  if (base && base.length > 0) {
    return base.replace(/\/$/, '');
  }
  return window.location.origin;
}

/**
 * Socket.IO reads a URL path as a namespace, and the server only serves the root
 * one. A server behind a sub-path (https://host/pipes) serves its socket at
 * https://host/pipes/socket.io, so connect to the origin and move the path into
 * the engine `path`.
 */
function getSocketEndpoint(baseUrl: string): { origin: string; path: string } {
  const url = new URL(baseUrl, window.location.origin);
  return { origin: url.origin, path: `${url.pathname.replace(/\/+$/, '')}/socket.io` };
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

  const { origin, path } = getSocketEndpoint(url);
  socket = io(origin, {
    path,
    transports: ['websocket', 'polling'],
    auth: bearerAuth(accessToken),
    autoConnect: true,
  });
  connectedWithToken = accessToken;
  return socket;
}

export function disconnectNotificationSocket(): void {
  teardownSocket();
}
