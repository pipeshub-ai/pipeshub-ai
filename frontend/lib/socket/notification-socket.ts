import { io, type Socket } from 'socket.io-client';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

/**
 * Reconnect timing mirrors the desktop socket (electron/local-sync/transport/desktop-socket.ts:
 * RECONNECT_DELAY_MS 2 s, RECONNECT_DELAY_MAX_MS 60 s, and `retryRefusedHandshake` for a
 * handshake the server refused, which socket.io does not retry on its own).
 */
export const RECONNECT_DELAY_MS = 2_000;
export const RECONNECT_DELAY_MAX_MS = 60_000;

/** The one refusal code that means "try again later": the server could not check the session. */
const SESSION_CHECK_UNAVAILABLE_CODE = 'SESSION_CHECK_UNAVAILABLE';
/** The server's whole refusal text for it, for servers that send no `data` with their refusals. */
const SESSION_CHECK_UNAVAILABLE_MESSAGE = 'Unable to check the session right now';

/**
 * True when a refused handshake may succeed later without a new token. Fails
 * closed: when the server sends `data` (`{ code, retryable }`), only the
 * temporary code with `retryable: true` is retried, so an auth code, an unknown
 * code or a flag that disagrees with its code never is. Without `data`, only the
 * exact session-check text is retried.
 */
function isRetryableRefusal(error: unknown): boolean {
  const data = (error as { data?: unknown } | null)?.data;
  if (data !== null && typeof data === 'object' && ('code' in data || 'retryable' in data)) {
    const { code, retryable } = data as { code?: unknown; retryable?: unknown };
    return code === SESSION_CHECK_UNAVAILABLE_CODE && retryable === true;
  }
  const message = (error as { message?: unknown } | null)?.message;
  return typeof message === 'string' && message.trim() === SESSION_CHECK_UNAVAILABLE_MESSAGE;
}

let socket: Socket | null = null;
/** Access token used for the current socket handshake (detect stale reuse). */
let connectedWithToken: string | null = null;
/** Pending retry of a refused handshake; at most one, always cleared on teardown. */
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let refusedHandshakes = 0;

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

function clearRetryTimer(): void {
  if (retryTimer !== null) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
}

function retryRefusedHandshake(target: Socket): void {
  if (retryTimer !== null) return;
  const ceiling = Math.min(RECONNECT_DELAY_MS * 2 ** refusedHandshakes, RECONNECT_DELAY_MAX_MS);
  // 50-100 % jitter so many tabs do not come back in the same instant.
  const delay = Math.round(ceiling * (0.5 + Math.random() * 0.5));
  refusedHandshakes += 1;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    if (socket !== target || target.connected) return;
    target.connect();
  }, delay);
}

function attachConnectionListeners(target: Socket): void {
  target.on('connect', () => {
    refusedHandshakes = 0;
    clearRetryTimer();
  });

  target.on('connect_error', (error: Error) => {
    if (isRetryableRefusal(error)) {
      // The server could not check the session for now. Not a sign-out:
      // the handshake was refused, which leaves socket.io inactive, so retry by hand.
      if (!target.active) retryRefusedHandshake(target);
      return;
    }
    // Anything else: a transport or network error is retried by socket.io itself
    // (target.active stays true). A refusal for a bad or expired token is not
    // retried here; the refreshed token reconnects through connectNotificationSocket.
  });
}

function teardownSocket(): void {
  clearRetryTimer();
  refusedHandshakes = 0;
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
    reconnection: true,
    reconnectionDelay: RECONNECT_DELAY_MS,
    reconnectionDelayMax: RECONNECT_DELAY_MAX_MS,
    randomizationFactor: 0.5,
  });
  connectedWithToken = accessToken;
  attachConnectionListeners(socket);
  return socket;
}

export function disconnectNotificationSocket(): void {
  teardownSocket();
}
