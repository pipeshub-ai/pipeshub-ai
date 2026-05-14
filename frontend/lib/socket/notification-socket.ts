import { io, type Socket } from 'socket.io-client';

let socket: Socket | null = null;

function getSocketBaseUrl(): string {
  if (typeof window === 'undefined') return '';
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (base && base.length > 0) {
    return base.replace(/\/$/, '');
  }
  return window.location.origin;
}

export function getNotificationSocket(): Socket | null {
  return socket;
}

export function connectNotificationSocket(accessToken: string | null): Socket | null {
  if (typeof window === 'undefined') return null;
  if (!accessToken) return null;
  if (socket?.connected) return socket;

  const url = getSocketBaseUrl();
  if (!url) return null;

  socket = io(url, {
    path: '/socket.io',
    transports: ['websocket', 'polling'],
    auth: {
      token: `Bearer ${accessToken}`,
    },
    autoConnect: true,
  });
  return socket;
}

export function disconnectNotificationSocket(): void {
  if (socket) {
    socket.disconnect();
    socket.removeAllListeners();
    socket = null;
  }
}
