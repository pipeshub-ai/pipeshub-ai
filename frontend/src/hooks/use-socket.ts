import { useEffect, useRef, useState, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import { CONFIG } from 'src/config-global';

interface UseSocketOptions {
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;
}

export const useSocket = (options?: UseSocketOptions) => {
  const [isConnected, setIsConnected] = useState(false);
  const socketRef = useRef<Socket | null>(null);
  const eventListenersRef = useRef<Map<string, Set<(...args: any[]) => void>>>(new Map());

  useEffect(() => {
    const token = localStorage.getItem('jwt_access_token');
    if (!token) {
      return;
    }

    // Use notificationBackendUrl if available, otherwise fallback to backendUrl
    const socketUrl = CONFIG.notificationBackendUrl || CONFIG.backendUrl;

    // Initialize Socket.IO connection
    const socket = io(socketUrl, {
      auth: {
        token: `Bearer ${token}`,
      },
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionAttempts: 5,
    });

    socketRef.current = socket;

    // Capture ref value at effect creation time for cleanup
    const listenersRef = eventListenersRef.current;

    socket.on('connect', () => {
      setIsConnected(true);
      // Re-register all event listeners when reconnected
      eventListenersRef.current.forEach((callbacks, event) => {
        callbacks.forEach((callback) => {
          socket.on(event, callback);
        });
      });
      options?.onConnect?.();
    });

    socket.on('disconnect', () => {
      setIsConnected(false);
      options?.onDisconnect?.();
    });

    socket.on('connect_error', (error) => {
      console.error('Socket.IO connection error:', error);
      options?.onError?.(error);
    });

    // Cleanup on unmount
    // eslint-disable-next-line consistent-return
    return () => {
      socket.disconnect();
      socketRef.current = null;
      // Use captured ref value from effect creation time
      if (listenersRef) {
        listenersRef.clear();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const emit = useCallback((event: string, data: any) => {
    if (socketRef.current && isConnected) {
      socketRef.current.emit(event, data);
    }
  }, [isConnected]);

  const on = useCallback((event: string, callback: (...args: any[]) => void) => {
    if (!eventListenersRef.current.has(event)) {
      eventListenersRef.current.set(event, new Set());
    }
    eventListenersRef.current.get(event)!.add(callback);

    if (socketRef.current) {
      if (isConnected) {
        socketRef.current.on(event, callback);
      } else {
        // If not connected yet, wait for connection
        socketRef.current.once('connect', () => {
          socketRef.current?.on(event, callback);
        });
      }
    }
  }, [isConnected]);

  const off = useCallback((event: string, callback?: (...args: any[]) => void) => {
    if (callback) {
      eventListenersRef.current.get(event)?.delete(callback);
    } else {
      eventListenersRef.current.delete(event);
    }

    if (socketRef.current) {
      if (callback) {
        socketRef.current.off(event, callback);
      } else {
        socketRef.current.off(event);
      }
    }
  }, []);

  return {
    socket: socketRef.current,
    isConnected,
    emit,
    on,
    off,
  };
};

