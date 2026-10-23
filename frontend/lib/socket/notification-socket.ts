import { io, type Socket } from 'socket.io-client';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

let socket: Socket | null = null;
/** Access token used for the current socket handshake (detect stale reuse). */
let connectedWithToken: string | null = null;

/** Retry delays after the server refused the handshake; socket.io itself never retries a refused one. */
export const REFUSED_RETRY_BASE_MS = 1_000;
export const REFUSED_RETRY_MAX_MS = 30_000;
let refusedRetryTimer: ReturnType<typeof setTimeout> | null = null;
let refusedAttempts = 0;

function clearRefusedRetry(): void {
  if (refusedRetryTimer) clearTimeout(refusedRetryTimer);
  refusedRetryTimer = null;
  refusedAttempts = 0;
}

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
  clearRefusedRetry();
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
  const created = io(origin, {
    path,
    transports: ['websocket', 'polling'],
    auth: bearerAuth(accessToken),
    autoConnect: true,
  });
  socket = created;
  connectedWithToken = accessToken;
  // A dropped connection reconnects by itself, but a handshake the server refused (an expired token after the
  // laptop slept, a restart mid-handshake) leaves the socket inactive for good: without this the pop-ups stop
  // until a reload. A new token replaces the socket, so these retries only ever send the current one.
  created.on('connect', () => {
    if (socket === created) clearRefusedRetry();
  });
  created.on('connect_error', () => {
    if (socket !== created || created.active || refusedRetryTimer) return;
    const delay = Math.min(REFUSED_RETRY_MAX_MS, REFUSED_RETRY_BASE_MS * 2 ** refusedAttempts);
    refusedAttempts += 1;
    refusedRetryTimer = setTimeout(() => {
      refusedRetryTimer = null;
      if (socket === created && !created.connected && !created.active) created.connect();
    }, delay);
  });
  return socket;
}

/**
 * Brings back a socket that is neither connected nor trying to be, at once: the tab came back into view or the
 * network came back, and waiting for the next scheduled retry would miss what arrives meanwhile.
 */
export function ensureNotificationSocketConnected(): void {
  if (!socket || socket.connected || socket.active) return;
  clearRefusedRetry();
  socket.connect();
}

export function disconnectNotificationSocket(): void {
  teardownSocket();
}
